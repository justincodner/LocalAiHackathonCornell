// Owner: Mac owner. Typed requests and "ask about part of the screen".
//
// - Type a request (Control+Option+Space, or the HUD keyboard button): a text field; Return emits `stt.final`
//   exactly like a spoken utterance (and like POST /debug/utterance), so the Brain handles it the same way.
//   While Beacon is asking a question, the typed text is the answer.
// - Ask about part of the screen (Control+Option+S, or the HUD viewfinder button): drag a rectangle; its text
//   (Vision OCR plus accessibility labels inside it) is attached as `focus` to the next `stt.final`, spoken or
//   typed. If no question comes within `selectionTimeout`, the selection is dropped.
import AppKit
import Foundation

@MainActor
final class PromptCoordinator {
    static var selectionTimeout: TimeInterval = 30

    private let services: Services
    private let hud: HUDController
    private let typed = TypedPromptPanel()
    private let selector = RegionSelector()
    private let shortcuts = ShortcutCenter()
    private let typedIDs = SequentialID(prefix: "u_typed")
    private var focusID: Int?
    private var expiryTask: Task<Void, Never>?
    private var readTask: Task<Void, Never>?

    init(services: Services, hud: HUDController) {
        self.services = services
        self.hud = hud
    }

    func start() {
        hud.typePrompt = { [weak self] in self?.openTypedPrompt() }
        hud.askAboutRegion = { [weak self] in self?.beginRegionSelection() }
        hud.clearSelection = { [weak self] in self?.removeSelectionAndReset() }
        typed.onSubmit = { [weak self] text in self?.submitTyped(text) }
        typed.onCancel = { [weak self] in self?.notify("Typing cancelled.", speak: false) }
        shortcuts.willFire = { [weak self] in self?.cancelTalkKeyRecording() }
        shortcuts.register(.typePrompt) { [weak self] in self?.openTypedPrompt() }
        shortcuts.register(.regionSnapshot) { [weak self] in self?.beginRegionSelection() }
        services.events.observe { [weak self] event in
            guard event["type"] as? String == "stt.final", event["focus"] != nil else { return }
            Task { @MainActor in self?.selectionConsumed() }
        }
    }

    /// Pressing Option as part of Control+Option+... may already have started a recording (Option pressed first).
    private func cancelTalkKeyRecording() {
        if services.speechIn.isListening { services.speechIn.cancelListening(reason: "shortcut") }
    }

    // MARK: Typed requests

    func openTypedPrompt() {
        guard !selector.isActive else { return }
        // The keyboard button and Control+Option+Space toggle: pressed again, they close the field.
        if typed.isVisible {
            typed.cancel()
            return
        }
        if hud.isMinimized { hud.restore() }
        let heading: String
        let placeholder: String
        if hud.model.showsSelection {
            heading = "Ask about your selection"
            placeholder = "Ask Beacon about the selection"
        } else if hud.model.state == .asking || hud.model.state == .confirm, let question = hud.model.pendingQuestion ?? nonEmpty(hud.model.caption) {
            heading = "Answer Beacon's question"
            placeholder = SelectionFocus.preview(question, limit: 60)
        } else {
            heading = "Ask Beacon a question"
            placeholder = "Ask Beacon a question"
        }
        typed.present(attachedTo: hud.pillPanel, heading: heading, placeholder: placeholder)
        Log.info("typed prompt opened (\(heading))")
    }

    private func nonEmpty(_ s: String) -> String? { s.isEmpty ? nil : s }

    private func submitTyped(_ text: String) {
        let s = services
        // Same as pressing the talk key: a new request stops Beacon's speech and cancels the running task.
        if s.speechOut.isSpeaking {
            s.speechOut.stop()
            s.events.emit("barge_in")
        }
        let id = typedIDs.next()
        Log.info("typed request \(id): \(text.count) characters")
        // Give the previous app a moment to take focus back, so actions and snapshots land there.
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(150))
            s.events.emit("stt.final", ["utterance_id": id, "text": text, "audio_ms": 0, "stt_ms": 0, "input": "typed"])
        }
    }

    // MARK: Region selection

    func beginRegionSelection() {
        guard !selector.isActive else { return }
        typed.cancel()
        guard CGPreflightScreenCaptureAccess() else {
            CGRequestScreenCaptureAccess()
            fail("Beacon needs Screen Recording permission to read part of the screen. "
                 + "Turn it on in System Settings, Privacy and Security, Screen Recording.")
            return
        }
        hud.play(.action)
        notify("Drag a rectangle around what you want to ask about. Press Escape to cancel.", speak: false)
        selector.begin { [weak self] rect in self?.regionSelected(rect) }
    }

    private func regionSelected(_ rect: CGRect?) {
        guard let rect else {
            notify("Selection cancelled.", speak: false)
            return
        }
        guard !RegionGeometry.isTooSmall(rect) else {
            notify("That selection was too small. Press \(hud.model.snapshotShortcut) and drag a larger area.", speak: true)
            return
        }
        hud.update(state: .thinking, transcript: "", caption: "Reading the selection…", latencyMS: nil)
        readTask?.cancel()
        let accessibility = services.accessibility
        readTask = Task { @MainActor [weak self] in
            let started = Date()
            do {
                let result = try await RegionReader.read(region: rect, accessibility: accessibility)
                guard !Task.isCancelled, let self else { return }
                let ms = Int(Date().timeIntervalSince(started) * 1000)
                Log.info("selection read: \(result.ocrLineCount) OCR lines, \(result.elementCount) elements, \(ms) ms")
                guard let focus = result.focus, let text = focus["text"] as? String else {
                    self.fail("I couldn't read any text in that area. Try selecting a larger area.")
                    return
                }
                self.selectionReady(focus: focus, text: text, image: result.image)
            } catch {
                guard let self else { return }
                Log.error("selection read failed: \(error)")
                self.fail("I couldn't read that part of the screen.")
            }
        }
    }

    private func selectionReady(focus: JSONObject, text: String, image: CGImage) {
        focusID = services.events.setPendingFocus(focus)
        hud.setSelection(preview: SelectionFocus.preview(text), image: image)
        hud.play(.listenStop)
        notify("Selection ready. Hold \(hud.model.talkKey) and ask about it, or press \(hud.model.typeShortcut) to type.",
               speak: true)
        scheduleExpiry()
    }

    private func scheduleExpiry() {
        expiryTask?.cancel()
        let id = focusID
        expiryTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(Self.selectionTimeout))
            // Still talking or typing: wait for them to finish.
            while !Task.isCancelled, let self, self.services.speechIn.isListening || self.typed.isVisible {
                try? await Task.sleep(for: .seconds(5))
            }
            guard !Task.isCancelled, let self, self.services.events.clearPendingFocus(id: id) else { return }
            self.focusID = nil
            self.hud.setSelection(preview: nil)
            Log.info("selection dropped after \(Int(Self.selectionTimeout)) s without a question")
            self.notify("No question came, so I cleared the selection.", speak: false)
        }
    }

    /// The thumbnail's X: drop the screenshot and its pending focus, and tell the Brain to start a fresh
    /// conversation (it stops any task in progress and forgets earlier exchanges).
    func removeSelectionAndReset() {
        services.events.clearPendingFocus()
        selectionConsumed()
        services.speechOut.stop()
        services.events.emit("conversation.reset", ["reason": "selection_removed"])
        hud.update(state: .idle, transcript: "", caption: "", latencyMS: nil)
        notify("Screenshot removed. Starting a new conversation.", speak: false)
    }

    private func selectionConsumed() {
        expiryTask?.cancel()
        expiryTask = nil
        focusID = nil
        hud.setSelection(preview: nil)
    }

    private func fail(_ message: String) {
        hud.play(.error)
        hud.update(state: .error, transcript: "", caption: message, latencyMS: nil)
        notify(message, speak: true)
    }

    /// VoiceOver users hear an announcement; everyone else hears Beacon's voice (when `speak`). Never both,
    /// so the two voices do not talk over each other.
    private func notify(_ text: String, speak: Bool) {
        if NSWorkspace.shared.isVoiceOverEnabled || !speak {
            hud.announce(text)
        } else {
            _ = services.speechOut.speak(text: text, interrupt: true, rate: nil)
        }
    }
}
