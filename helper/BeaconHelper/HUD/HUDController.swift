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
    private var observers: [NSObjectProtocol] = []
    /// Number of thinking ticks played so far (for tests and logs).
    private(set) var ticksPlayed = 0
    var pillPanel: PillPanel { pill }
    var ringPanel: OverlayPanel { ring.panel }

    init() {
        let host = NSHostingView(rootView: PillView(model: model))
        host.sizingOptions = []
        host.frame = NSRect(origin: .zero, size: PillPanel.size)
        host.autoresizingMask = [.width, .height]
        pill.contentView = host

        let ws = NSWorkspace.shared.notificationCenter
        observers.append(ws.addObserver(forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.model.reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
            }
        })
        observers.append(NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                                                object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.pill.reposition() }
        })
    }

    func show() {
        pill.reposition()
        pill.orderFrontRegardless()   // never makeKeyAndOrderFront / NSApp.activate
    }

    func update(state: HUDState?, transcript: String?, caption: String?, latencyMS: Int?) {
        if let transcript {
            model.transcript = transcript
            if caption == nil { model.caption = "" }   // a new transcript starts a new turn
        }
        if let caption { model.caption = caption }
        if let latencyMS { model.latencyMS = latencyMS }
        if let state, state != model.state {
            model.state = state
            stateChanged(to: state)
        }
        if !pill.isVisible { show() }
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
