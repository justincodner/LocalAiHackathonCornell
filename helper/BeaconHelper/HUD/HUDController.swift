// Owner: Mac owner. The HUD (H6): floating pill, highlight ring, sound cues and the thinking tick.
import AppKit
import SwiftUI

@MainActor
final class HUDController: HUDProviding {
    /// Spec 9.2: after 1.5 s of thinking, tick once a second until the state changes.
    static let tickDelay: TimeInterval = 1.5
    static let tickInterval: TimeInterval = 1.0

    let model = HUDModel()
    private let pill = PillPanel()
    private let ring = HighlightRing()
    private let sounds = SoundPlayer()
    private var tickTask: Task<Void, Never>?
    private var revealTask: Task<Void, Never>?
    private var activeSpeechID: String?
    private var hasExplicitCaption = false
    private var observers: [NSObjectProtocol] = []
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
            selectPosition: { [weak self] placement in
                self?.pill.move(to: placement)
                self?.show()
            }
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
        layout()
        pill.orderFrontRegardless()   // never makeKeyAndOrderFront / NSApp.activate
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
        if let state, state != model.state {
            model.state = state
            stateChanged(to: state)
        }
        if let caption {
            hasExplicitCaption = !caption.isEmpty
            setCaption(caption, reveal: model.state == .speaking || model.state == .idle)
        }
        if !pill.isVisible { show() }
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
        if model.state != .confirm {
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
        pill.layout(reply: model.visibleCaption)
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
            try? await Task.sleep(nanoseconds: UInt64(Self.tickDelay * 1_000_000_000))
            while !Task.isCancelled, let self, self.model.state == .thinking {
                self.sounds.play(.tick)
                self.ticksPlayed += 1
                try? await Task.sleep(nanoseconds: UInt64(Self.tickInterval * 1_000_000_000))
            }
        }
    }
}
