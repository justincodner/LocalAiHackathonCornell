// Owner: Mac owner. The HUD (H6): floating pill, highlight ring, sound cues and the thinking tick.
import AppKit
import SwiftUI

@MainActor
final class HUDController: HUDProviding {
    /// Spec 9.2: after 1.5 s of thinking, tick once a second until the state changes.
    static let tickDelay: TimeInterval = 1.5
    static let tickInterval: TimeInterval = 1.0
    /// If the Brain hangs or dies mid-request, stop thinking after this long. Bonsai 4B can take ~35 s on a big screen.
    static var thinkingTimeout: TimeInterval = 90
    static let thinkingTimeoutMessage = "I didn't get an answer. Please try again."

    let model = HUDModel()
    private let pill = PillPanel()
    private let ring = HighlightRing()
    private let sounds = SoundPlayer()
    private var tickTask: Task<Void, Never>?
    private var revealTask: Task<Void, Never>?
    private var activeSpeechID: String?
    private var hasExplicitCaption = false
    private var observers: [NSObjectProtocol] = []
    /// Read off the main thread by the EventHub to decide whether "Hey Beacon" should restore the HUD.
    let minimizedFlag = LockedValue(false)
    private(set) var isMinimized = false {
        didSet { minimizedFlag.set(isMinimized) }
    }
    /// Set once the user starts a new request after minimizing, so updates for that request bring the HUD back
    /// but updates still arriving for the request in flight when they minimized do not.
    private var newRequestSinceMinimize = false
    /// Called by the Turn off button. Defaults to quitting the app (which runs AppDelegate's terminate cleanup).
    var turnOff: () -> Void = { NSApp.terminate(nil) }
    /// "Use 4B model" in the ⋯ menu. Set by AppDelegate; receives the requested state.
    var toggleQuestionModel: (_ enable: Bool) -> Void = { _ in }
    /// Keyboard and viewfinder buttons. Set by PromptCoordinator.
    var typePrompt: () -> Void = {}
    var askAboutRegion: () -> Void = {}
    /// The X on the selection thumbnail. Set by PromptCoordinator.
    var clearSelection: () -> Void = {}
    /// Number of thinking ticks played so far (for tests and logs).
    private(set) var ticksPlayed = 0
    var pillPanel: PillPanel { pill }
    var ringPanel: OverlayPanel { ring.panel }

    init() {
        let host = NSHostingView(rootView: PillView(
            model: model,
            dragHeader: { [weak self] event in
                guard let self else { return }
                self.pill.dragHeader(with: event) { self.layout() }
            },
            minimize: { [weak self] in self?.minimize() },
            turnOff: { [weak self] in self?.turnOff() },
            toggleQuestionModel: { [weak self] in self?.requestQuestionModelToggle() },
            typePrompt: { [weak self] in self?.typePrompt() },
            askAboutRegion: { [weak self] in self?.askAboutRegion() },
            clearSelection: { [weak self] in self?.clearSelection() }
        ))
        host.sizingOptions = []
        host.frame = NSRect(origin: .zero, size: PillPanel.size)
        host.autoresizingMask = [.width, .height]
        pill.contentView = host

        let ws = NSWorkspace.shared.notificationCenter
        observers.append(ws.addObserver(forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.model.reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
                self?.model.reduceTransparency = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
                if self?.model.reduceMotion == true { self?.finishReveal() }
            }
        })
        observers.append(NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                                                object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.layout() }
        })
    }

    func show() {
        isMinimized = false
        layout()
        pill.orderFrontRegardless()   // never makeKeyAndOrderFront / NSApp.activate
    }

    // MARK: Minimize and restore

    /// Hides the HUD (no Dock miniaturize: the Helper has no Dock icon). "Hey Beacon" or a new request brings it back.
    func minimize() {
        guard !isMinimized else { return }
        isMinimized = true
        newRequestSinceMinimize = false
        pill.orderOut(nil)
        ring.panel.orderOut(nil)
        Log.info("HUD minimized")
        announce("Beacon minimized. Press \(model.talkKey) and say Hey Beacon to bring it back.")
    }

    /// Shows a minimized HUD again in the top corner that covers less of the frontmost window.
    func restore(wakeOnly: Bool = false) {
        if wakeOnly, model.state == .listening {
            update(state: model.pendingQuestion == nil ? .idle : .asking, transcript: "",
                   caption: model.pendingQuestion, latencyMS: nil)
        }
        guard isMinimized else { return }
        placeAtRestoreCorner()
        show()
        Log.info("HUD restored")
    }

    private func placeAtRestoreCorner() {
        let window = FrontWindow.frame()
        let screen = window.flatMap { w in NSScreen.screens.first { $0.frame.contains(NSPoint(x: w.midX, y: w.midY)) } }
            ?? NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) }
            ?? NSScreen.main
        guard let screen else { return }
        let placement = HUDRestorePlacement.choose(visibleFrame: screen.visibleFrame, frontWindow: window)
        pill.move(to: placement, in: screen.visibleFrame)
    }

    /// Whether an update with this state should bring back a minimized HUD.
    /// Listening (the user pressed the talk key) and idle stay hidden; questions always show; anything else shows
    /// only for a request started after the HUD was minimized.
    static func revealsMinimizedHUD(state: HUDState?, newRequestSinceMinimize: Bool) -> Bool {
        switch state {
        case nil, .idle?, .listening?: return false
        case .asking?, .confirm?: return true
        default: return newRequestSinceMinimize
        }
    }

    // MARK: 4B question model (⋯ menu)

    func setQuestionModel(_ state: QuestionModelState) {
        model.questionModel = state
    }

    /// Asks the Brain to load or unload the 4B. Shows "loading" right away; the Brain reports the outcome.
    func requestQuestionModelToggle() {
        guard model.questionModel != .loading else { return }
        let enable = model.questionModel == .off
        if enable { model.questionModel = .loading }
        toggleQuestionModel(enable)
    }

    // MARK: Selection (Control+Option+S)

    /// Shows (preview text and a thumbnail of the captured region) or clears (nil) the "Ask about this selection" look.
    func setSelection(preview: String?, image: CGImage? = nil) {
        model.selectionPreview = preview
        model.selectionImage = preview == nil ? nil : image.map { NSImage(cgImage: $0, size: .zero) }
        if preview != nil {
            if isMinimized { placeAtRestoreCorner() }
            update(state: .idle, transcript: "", caption: "", latencyMS: nil)
            show()
        } else {
            layout()
        }
    }

    /// VoiceOver announcement (high priority, interrupts other speech from VoiceOver).
    func announce(_ text: String) {
        NSAccessibility.post(element: NSApp as Any, notification: .announcementRequested,
                             userInfo: [.announcement: text,
                                        .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }

    func update(state: HUDState?, transcript: String?, caption: String?, latencyMS: Int?) {
        if let transcript {
            if caption == nil, transcript != model.transcript || state == .listening {
                activeSpeechID = nil
                hasExplicitCaption = false
                setCaption("", reveal: false)
                model.latencyMS = nil
            }
            model.transcript = transcript
        }
        if let latencyMS { model.latencyMS = latencyMS }
        if let state {
            model.pendingQuestion = Self.pendingQuestion(after: state, caption: caption, current: model.pendingQuestion)
        }
        if let state, state != model.state {
            model.state = state
            stateChanged(to: state)
        }
        if let caption {
            hasExplicitCaption = !caption.isEmpty
            setCaption(caption, reveal: model.state == .speaking || model.state == .idle)
        }
        if isMinimized {
            if state == .listening { newRequestSinceMinimize = true }
            if Self.revealsMinimizedHUD(state: state, newRequestSinceMinimize: newRequestSinceMinimize) {
                placeAtRestoreCorner()
                show()
            } else {
                layout()
            }
        } else if !pill.isVisible {
            show()
        }
    }

    /// The question still waiting for an answer after a state change. Asking sets it; listening (the answer)
    /// keeps it; any other state means the answer is in, or Beacon moved on, so it is cleared.
    static func pendingQuestion(after state: HUDState, caption: String?, current: String?) -> String? {
        switch state {
        case .asking:
            if let caption, !caption.isEmpty { return caption }
            return current
        case .listening: return current
        default: return nil
        }
    }

    /// A recording was dropped (too short, no speech). Go back to the question if one is waiting, else idle.
    func listenCancelled() {
        if let question = model.pendingQuestion, model.state == .listening {
            update(state: .asking, transcript: "", caption: question, latencyMS: nil)
        } else {
            update(state: .idle, transcript: nil, caption: nil, latencyMS: nil)
        }
    }

    func move(to placement: HUDPlacement) {
        pill.move(to: placement)
        if model.state == .listening || model.state == .thinking {
            update(state: .idle, transcript: "", caption: nil, latencyMS: nil)
        }
        play(.action)
        show()
    }

    func speechStarted(id: String, text: String) {
        activeSpeechID = id
        // "Selection ready" prompts keep the selection on screen instead of a speaking caption.
        if model.showsSelection, model.state == .idle { return }
        if model.state != .confirm, model.state != .asking {
            update(state: .speaking, transcript: nil, caption: nil, latencyMS: nil)
        }
        if !hasExplicitCaption { setCaption(text, reveal: true) }
    }

    func speechFinished(id: String, interrupted: Bool) {
        guard id == activeSpeechID else { return }
        activeSpeechID = nil
        // Keep replies animating when a short utterance finishes before its last words appear.
        if interrupted { finishReveal() }
        if model.state == .speaking {
            update(state: .idle, transcript: nil, caption: nil, latencyMS: nil)
        }
    }

    private func setCaption(_ text: String, reveal: Bool) {
        guard text != model.caption else {
            if !reveal { finishReveal() }
            return
        }
        let previous = model.visibleCaption
        revealTask?.cancel()
        model.caption = text
        guard reveal, !model.reduceMotion, !text.isEmpty else {
            finishReveal()
            return
        }

        // Retain the portion already displayed if a caller sends a cumulative caption update.
        var end = text.hasPrefix(previous)
            ? text.index(text.startIndex, offsetBy: previous.count, limitedBy: text.endIndex) ?? text.endIndex
            : text.startIndex
        let step = max(4, text.count / 90)
        end = text.index(end, offsetBy: step, limitedBy: text.endIndex) ?? text.endIndex
        model.revealedCaption = String(text[..<end])
        layout()
        revealTask = Task { @MainActor [weak self] in
            while end < text.endIndex {
                try? await Task.sleep(for: .milliseconds(22))
                guard !Task.isCancelled, let self else { return }
                end = text.index(end, offsetBy: step, limitedBy: text.endIndex) ?? text.endIndex
                self.model.revealedCaption = String(text[..<end])
                self.layout()
            }
        }
    }

    private func finishReveal() {
        revealTask?.cancel()
        revealTask = nil
        model.revealedCaption = nil
        layout()
    }

    private func layout() {
        pill.layout(reply: model.replyText, question: model.replyLabel != nil,
                    thumbnail: model.showsSelectionThumbnail ? model.selectionImage?.size : nil)
        if model.replyAboveHeader != pill.replyAboveHeader {
            model.replyAboveHeader = pill.replyAboveHeader
        }
    }

    func highlight(_ rect: CGRect) {
        ring.show(axRect: rect, reduceMotion: model.reduceMotion)
    }

    func setOffline(_ offline: Bool) {
        model.offline = offline
    }

    func play(_ cue: SoundCue) {
        sounds.play(cue)
    }

    var windowNumbers: [Int] {
        [pill.windowNumber, ring.panel.windowNumber].filter { $0 > 0 }
    }

    private func stateChanged(to state: HUDState) {
        tickTask?.cancel()
        tickTask = nil
        guard state == .thinking else { return }
        tickTask = Task { @MainActor [weak self] in
            let started = Date()
            try? await Task.sleep(nanoseconds: UInt64(Self.tickDelay * 1_000_000_000))
            while !Task.isCancelled, let self, self.model.state == .thinking {
                if Date().timeIntervalSince(started) >= Self.thinkingTimeout {
                    Log.error("HUD thinking for \(Int(Self.thinkingTimeout)) s with no Brain update; showing error")
                    self.sounds.play(.error)
                    self.update(state: .error, transcript: nil, caption: Self.thinkingTimeoutMessage, latencyMS: nil)
                    return
                }
                self.sounds.play(.tick)
                self.ticksPlayed += 1
                try? await Task.sleep(nanoseconds: UInt64(Self.tickInterval * 1_000_000_000))
            }
        }
    }
}

/// Frame of the frontmost app's focused window, via Accessibility (best effort).
enum FrontWindow {
    /// AppKit coordinates (bottom-left origin), or nil when there is no other app's window or AX is unavailable.
    @MainActor
    static func frame() -> NSRect? {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return nil }
        let element = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(element, 0.3)
        var window: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXFocusedWindowAttribute as CFString, &window) == .success,
              let window, CFGetTypeID(window) == AXUIElementGetTypeID() else { return nil }
        let win = window as! AXUIElement
        var positionRef: CFTypeRef?
        var sizeRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(win, kAXPositionAttribute as CFString, &positionRef) == .success,
              AXUIElementCopyAttributeValue(win, kAXSizeAttribute as CFString, &sizeRef) == .success,
              let positionRef, let sizeRef else { return nil }
        var origin = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(positionRef as! AXValue, .cgPoint, &origin),
              AXValueGetValue(sizeRef as! AXValue, .cgSize, &size),
              let primary = NSScreen.screens.first else { return nil }
        return appKitRect(fromAXRect: CGRect(origin: origin, size: size), primaryScreenHeight: primary.frame.height)
    }
}
