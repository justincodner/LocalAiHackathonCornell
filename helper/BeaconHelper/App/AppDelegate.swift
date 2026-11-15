// Owner: Mac owner. Menu-bar agent app: permissions, server start-up, push-to-talk wiring, prewarm.
import AppKit
import Foundation

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var hotkey: HotkeyMonitor?
    private var server: HelperServer?
    private var services: Services!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)   // LSUIElement: no Dock icon, never frontmost
        Permissions.requestAtLaunch()

        let hud = HUDController()
        let events = EventHub { placement in
            Task { @MainActor in hud.move(to: placement) }
        }
        let accessibility = AccessibilityService()
        // @Sendable so the AX read runs off the main actor, on the speech service's detached task.
        var screenText: (@Sendable () async -> ScreenText?)?
        if Config.sttScreenHints {
            screenText = { @Sendable in accessibility.screenText() }
        }
        services = Services(accessibility: accessibility,
                            input: InputService(),
                            speechIn: SpeechInputService(events: events, screenText: screenText),
                            speechOut: SpeechOutputService(events: events),
                            events: events,
                            hud: hud)
        Services.shared = services
        hud.show()

        Connectivity.shared.onChange = { online in
            Task { @MainActor in hud.setOffline(!online) }
        }
        Connectivity.shared.start()
        hud.setOffline(!Connectivity.shared.isOnline)

        wireEventsToHUD(hud)
        observeFrontmostApp()
        startHotkey()
        buildMenu()

        let server = HelperServer(services: services)
        self.server = server
        Task.detached {
            do { try await server.start() } catch {
                Log.error("server failed: \(error)")
            }
        }

        let s = services!
        Task.detached {
            async let stt: Void = s.speechIn.prewarm()
            async let tts: Void = s.speechOut.prewarm()
            _ = await (stt, tts)
            Log.info("prewarm finished")
        }
    }

    // MARK: Push-to-talk and barge-in (spec 9.1, 9.5)

    private func startHotkey() {
        let hotkey = HotkeyMonitor()
        let s = services!
        hotkey.onPress = {
            if s.speechOut.isSpeaking {
                s.speechOut.stop()                    // stop audio first, then tell the Brain
                s.events.emit("barge_in")
            }
            s.hud.play(.listenStart)
            s.speechIn.startListening()
            s.hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        }
        hotkey.onRelease = {
            guard s.speechIn.isListening else { return }
            s.speechIn.stopListening()
        }
        hotkey.start()
        self.hotkey = hotkey
    }

    static let brainOfflineMessage = "Beacon's brain isn't running, so I can't do that. Restart it with run all."

    /// Mirrors speech events onto the HUD so captions work even before the Brain posts /hud.
    private func wireEventsToHUD(_ hud: HUDController) {
        let events = services.events
        let speechOut = services.speechOut
        events.observe { event in
            let type = event["type"] as? String ?? ""
            // The Brain is the only /events client. Without one, nothing will answer this request.
            let brainConnected = events.clientCount > 0
            let text = event["text"] as? String
            let speechID = event["speech_id"] as? String
            let interrupted = event["interrupted"] as? Bool ?? false
            Task { @MainActor in
                switch type {
                case "stt.partial":
                    hud.update(state: .listening, transcript: text, caption: nil, latencyMS: nil)
                case "stt.final":
                    hud.play(.listenStop)
                    if brainConnected {
                        hud.update(state: .thinking, transcript: text, caption: nil, latencyMS: nil)
                    } else {
                        Log.error("stt.final with no Brain connected to /events; request dropped")
                        hud.play(.error)
                        hud.update(state: .error, transcript: text, caption: Self.brainOfflineMessage, latencyMS: nil)
                        _ = speechOut.speak(text: Self.brainOfflineMessage, interrupt: true, rate: nil)
                    }
                case "listen.cancelled":
                    hud.play(.error)
                    hud.update(state: .idle, transcript: nil, caption: nil, latencyMS: nil)
                case "tts.started":
                    if let speechID, let text { hud.speechStarted(id: speechID, text: text) }
                case "tts.finished":
                    if let speechID { hud.speechFinished(id: speechID, interrupted: interrupted) }
                default: break
                }
            }
        }
    }

    private func observeFrontmostApp() {
        let ownPID = ProcessInfo.processInfo.processIdentifier
        let events = services.events
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { note in
            guard let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                  app.processIdentifier != ownPID else { return }
            events.emit("app.changed", ["app": app.localizedName ?? "", "window": WindowTitle.front(of: app.processIdentifier) ?? ""])
        }
    }

    // MARK: Menu bar

    private func buildMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Beacon")
        let menu = NSMenu()
        menu.addItem(withTitle: "Beacon Helper \(Config.version)", action: nil, keyEquivalent: "")
        menu.addItem(withTitle: "Listening on 127.0.0.1:\(Config.port)", action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Open Accessibility Settings…", action: #selector(openAXSettings), keyEquivalent: "")
            .target = self
        menu.addItem(withTitle: "Quit Beacon Helper", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu
        statusItem = item
    }

    @objc private func openAXSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }
}

/// Reads the focused window title of an app via accessibility (best effort, for `app.changed`).
enum WindowTitle {
    static func front(of pid: pid_t) -> String? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.4)
        var win: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &win) == .success,
              let win else { return nil }
        var title: CFTypeRef?
        guard AXUIElementCopyAttributeValue(win as! AXUIElement, kAXTitleAttribute as CFString, &title) == .success else { return nil }
        return title as? String
    }
}
