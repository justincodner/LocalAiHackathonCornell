// Owner: Mac owner. HUD (H6) unit checks: focus safety, coordinates, caption rules, thinking tick, sounds.
import AppKit
import SwiftUI
import XCTest
@testable import BeaconHelper

@MainActor
final class HUDTests: XCTestCase {
    func testPanelsNeverTakeFocus() {
        let hud = HUDController()
        hud.show()
        for panel in [hud.pillPanel as NSPanel, hud.ringPanel] {
            XCTAssertFalse(panel.canBecomeKey)
            XCTAssertFalse(panel.canBecomeMain)
            XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
            XCTAssertFalse(panel.hidesOnDeactivate)
            XCTAssertTrue(panel.collectionBehavior.contains(.canJoinAllSpaces))
        }
        XCTAssertFalse(hud.pillPanel.ignoresMouseEvents)
        XCTAssertTrue(hud.ringPanel.ignoresMouseEvents)
        XCTAssertEqual(hud.pillPanel.frame.size, PillPanel.size)
        XCTAssertFalse(hud.pillPanel.isKeyWindow)
    }

    func testVoicePlacementIsLocalAndOrdinaryUtterancesArePublished() {
        let placements = LockedValue<[HUDPlacement]>([])
        let hub = EventHub { placements.set(placements.get() + [$0]) }
        hub.emit("stt.final", ["text": "Move Beacon to the top-right corner."])
        hub.emit("stt.final", ["text": "Put the overlay in the centre."])
        hub.emit("stt.final", ["text": "Move the meeting to the left of lunch."])
        XCTAssertEqual(placements.get(), [.topRight, .center])
        XCTAssertEqual(hub.recent.compactMap { $0["text"] as? String }, ["Move the meeting to the left of lunch."])
    }

    func testReplyExpandsWithinTheScreenAndCollapsesWhenCleared() throws {
        let screen = try XCTUnwrap(NSScreen.main)
        let preferences = try XCTUnwrap(UserDefaults(suiteName: "BeaconTests.\(UUID())"))
        let panel = PillPanel(preferences: preferences)
        panel.layout(reply: "")
        let initialFrame = panel.frame
        panel.layout(reply: String(repeating: "Beacon reply with several lines.\n", count: 60))
        XCTAssertTrue(screen.visibleFrame.contains(panel.frame))
        XCTAssertEqual(panel.frame.minY, initialFrame.minY)
        XCTAssertGreaterThan(panel.frame.height, initialFrame.height)
        XCTAssertLessThanOrEqual(panel.frame.height, initialFrame.height + 280)
        panel.layout(reply: "")
        XCTAssertEqual(panel.frame, initialFrame)
    }

    func testAXRectConversion() {
        // A 100x50 element 20 pt from the top of a 1000-pt-high primary screen.
        let r = appKitRect(fromAXRect: CGRect(x: 10, y: 20, width: 100, height: 50), primaryScreenHeight: 1000)
        XCTAssertEqual(r, NSRect(x: 10, y: 930, width: 100, height: 50))
    }

    func testHighlightShowsRingAroundRect() throws {
        let hud = HUDController()
        let h = try XCTUnwrap(NSScreen.screens.first).frame.height
        hud.highlight(CGRect(x: 200, y: 100, width: 120, height: 40))
        XCTAssertTrue(hud.ringPanel.isVisible)
        let expected = NSRect(x: 200, y: h - 140, width: 120, height: 40).insetBy(dx: -RingView.padding, dy: -RingView.padding)
        XCTAssertEqual(hud.ringPanel.frame, expected)
        XCTAssertEqual(hud.windowNumbers.count, 2)
    }

    func testCaptionReplacesTranscriptAndNewTranscriptClearsCaption() {
        let hud = HUDController()
        hud.update(state: .listening, transcript: "open mail", caption: nil, latencyMS: nil)
        XCTAssertEqual(hud.model.displayText, "open mail")
        hud.update(state: .speaking, transcript: nil, caption: "Mail is open.", latencyMS: 1630)
        XCTAssertEqual(hud.model.displayText, "Mail is open.")
        XCTAssertEqual(hud.model.latencyMS, 1630)
        hud.update(state: .idle, transcript: nil, caption: nil, latencyMS: nil)
        XCTAssertEqual(hud.model.displayText, "Mail is open.")
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        XCTAssertEqual(hud.model.displayText, "")
    }

    func testThinkingTicksAfterDelayAndStopsOnStateChange() async throws {
        let hud = HUDController()
        hud.update(state: .thinking, transcript: "summarise", caption: nil, latencyMS: nil)
        try await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(hud.ticksPlayed, 0, "no tick before 1.5 s")
        try await Task.sleep(nanoseconds: 1_500_000_000)   // t = 2.7 s: ticks at 1.5 and 2.5
        XCTAssertEqual(hud.ticksPlayed, 2)
        hud.update(state: .speaking, transcript: nil, caption: nil, latencyMS: nil)
        try await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(hud.ticksPlayed, 2, "ticks stop when the state changes")
    }

    func testThinkingTimesOutToError() async throws {
        let saved = HUDController.thinkingTimeout
        HUDController.thinkingTimeout = 2
        defer { HUDController.thinkingTimeout = saved }
        let hud = HUDController()
        hud.update(state: .thinking, transcript: "send Emmett hello", caption: nil, latencyMS: nil)
        try await Task.sleep(nanoseconds: 3_200_000_000)
        XCTAssertEqual(hud.model.state, .error)
        XCTAssertEqual(hud.model.caption, HUDController.thinkingTimeoutMessage)
    }

    func testSoundsAreFoundInBuiltBundle() {
        let dir = SoundPlayer.soundsDirectory()
        // The test bundle is not Bundle.main; just make sure lookup does not crash and, if found, has every cue.
        if let dir {
            for cue in SoundCue.allCases {
                XCTAssertTrue(FileManager.default.fileExists(atPath: dir.appendingPathComponent(cue.rawValue + ".wav").path))
            }
        }
        SoundPlayer().play(.action)   // must not crash even when sounds are missing
    }

    // MARK: Asking and answering

    func testAskingStateParsesFromTheHUDRoute() {
        XCTAssertEqual(HUDState(rawValue: "asking"), .asking)
        XCTAssertTrue(HUDState.allCases.contains(.asking))
        XCTAssertNil(HUDState(rawValue: "ask"))
    }

    func testAskingAndAnsweringLabels() {
        let asking = HUDStatusText.of(state: .asking, pendingQuestion: "Which Sarah?", transcript: "send Sarah hi")
        XCTAssertEqual(asking, HUDStatusText(title: "Beacon is asking", word: nil,
                                             detail: "Hold Option to answer", detailIsHint: true))
        let answering = HUDStatusText.of(state: .listening, pendingQuestion: "Which Sarah?", transcript: "")
        XCTAssertEqual(answering.title, "Answering Beacon's question")
        XCTAssertEqual(answering.detail, "Say your answer, then release Option")
        XCTAssertTrue(answering.detailIsHint)
        let heard = HUDStatusText.of(state: .listening, pendingQuestion: "Which Sarah?", transcript: "Sarah Chen")
        XCTAssertEqual(heard.detail, "Sarah Chen")
        XCTAssertFalse(heard.detailIsHint)
        XCTAssertEqual(heard.accessibilityLabel, "Answering Beacon's question, You said: Sarah Chen")
        let fresh = HUDStatusText.of(state: .listening, pendingQuestion: nil, transcript: "")
        XCTAssertEqual(fresh, HUDStatusText(title: "Beacon", word: "Listening", detail: nil, detailIsHint: false))
        XCTAssertEqual(HUDStatusText.of(state: .asking, pendingQuestion: "Q", transcript: "", talkKey: "Fn").detail,
                       "Hold Fn to answer")
        XCTAssertEqual(HUDStatusText.talkKeyName("right_option"), "Option")
        XCTAssertEqual(HUDStatusText.talkKeyName("right_cmd"), "Command")
    }

    func testPendingQuestionLifecycle() {
        XCTAssertEqual(HUDController.pendingQuestion(after: .asking, caption: "Which Sarah?", current: nil), "Which Sarah?")
        XCTAssertEqual(HUDController.pendingQuestion(after: .asking, caption: nil, current: "Q"), "Q")
        XCTAssertEqual(HUDController.pendingQuestion(after: .listening, caption: nil, current: "Q"), "Q")
        for state in [HUDState.thinking, .idle, .error, .speaking, .acting, .confirm] {
            XCTAssertNil(HUDController.pendingQuestion(after: state, caption: nil, current: "Q"), "\(state)")
        }
    }

    func testAnsweringKeepsTheQuestionVisibleUntilTheAnswerIsIn() {
        let hud = HUDController()
        hud.update(state: .asking, transcript: nil, caption: "Which Sarah, Chen or Lee?", latencyMS: nil)
        XCTAssertEqual(hud.model.pendingQuestion, "Which Sarah, Chen or Lee?")
        XCTAssertEqual(hud.model.replyText, "Which Sarah, Chen or Lee?")
        XCTAssertEqual(hud.model.replyLabel, "Question")
        // Speaking the question must not turn the asking state into plain speaking.
        hud.speechStarted(id: "s1", text: "Which Sarah, Chen or Lee? Press Option, then say your answer.")
        XCTAssertEqual(hud.model.state, .asking)
        hud.speechFinished(id: "s1", interrupted: true)
        XCTAssertEqual(hud.model.state, .asking)

        // The talk key: same call the hotkey makes.
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        XCTAssertTrue(hud.model.isAnswering)
        XCTAssertEqual(hud.model.statusText.title, "Answering Beacon's question")
        XCTAssertEqual(hud.model.replyText, "Which Sarah, Chen or Lee?")

        // A dropped recording goes back to the question rather than to idle.
        hud.listenCancelled()
        XCTAssertEqual(hud.model.state, .asking)
        XCTAssertEqual(hud.model.pendingQuestion, "Which Sarah, Chen or Lee?")

        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        hud.update(state: .thinking, transcript: "Sarah Chen", caption: nil, latencyMS: nil)
        XCTAssertNil(hud.model.pendingQuestion)
        XCTAssertFalse(hud.model.isAnswering)
        XCTAssertEqual(hud.model.statusText.title, "Beacon")
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        XCTAssertEqual(hud.model.statusText.word, "Listening", "a later request is a fresh request")
        XCTAssertNil(hud.model.replyLabel)
    }

    // MARK: Wake phrase, minimize and restore

    func testWakePhraseDetection() {
        XCTAssertEqual(WakePhrase.match("Hey Beacon."), .only)
        XCTAssertEqual(WakePhrase.match("hey beacon"), .only)
        XCTAssertEqual(WakePhrase.match("Hi, Beacon!"), .only)
        XCTAssertEqual(WakePhrase.match("Beacon?"), .only)
        XCTAssertEqual(WakePhrase.match("Okay Beacon"), .only)
        XCTAssertEqual(WakePhrase.match("Hey Beacon, open Mail."), .followedBy("open Mail."))
        XCTAssertEqual(WakePhrase.match("Hey Beacon what's on my screen"), .followedBy("what's on my screen"))
        XCTAssertEqual(WakePhrase.match("Beacon: read the first email"), .followedBy("read the first email"))
        XCTAssertNil(WakePhrase.match("Open Mail"))
        XCTAssertNil(WakePhrase.match("Hey Sarah"))
        XCTAssertNil(WakePhrase.match("Hey"))
        XCTAssertNil(WakePhrase.match(""))
        XCTAssertNil(WakePhrase.match("Search for beacon lights"), "Beacon must lead the utterance")
    }

    func testWakePhraseIsInterceptedOnlyWhileMinimized() {
        let minimized = LockedValue(true)
        let restores = LockedValue<[Bool]>([])
        let placements = LockedValue<[HUDPlacement]>([])
        let hub = EventHub(moveHUD: { placements.set(placements.get() + [$0]) },
                           restoreHUD: { restores.set(restores.get() + [$0]) },
                           isHUDMinimized: { minimized.get() })
        hub.emit("stt.final", ["text": "Hey Beacon."])
        hub.emit("stt.final", ["text": "Hey Beacon, open Mail."])
        hub.emit("stt.final", ["text": "Hey Beacon, move Beacon to the top left."])
        hub.emit("stt.final", ["text": "Open Safari"])
        XCTAssertEqual(restores.get(), [true, false, false])
        XCTAssertEqual(placements.get(), [.topLeft])
        XCTAssertEqual(hub.recent.compactMap { $0["text"] as? String }, ["open Mail.", "Open Safari"])

        minimized.set(false)
        hub.emit("stt.final", ["text": "Hey Beacon."])
        XCTAssertEqual(restores.get().count, 3, "not minimized: the wake phrase goes to the Brain as said")
        XCTAssertEqual(hub.recent.last?["text"] as? String, "Hey Beacon.")
    }

    func testMinimizedHUDStaysHiddenWhileListeningAndReturnsForANewRequest() {
        let hud = HUDController()
        hud.update(state: .thinking, transcript: "summarise this", caption: nil, latencyMS: nil)
        XCTAssertTrue(hud.pillPanel.isVisible)
        hud.minimize()
        XCTAssertTrue(hud.isMinimized)
        XCTAssertTrue(hud.minimizedFlag.get())
        XCTAssertFalse(hud.pillPanel.isVisible)
        // Updates for the request already in flight do not undo Minimize.
        hud.update(state: .speaking, transcript: nil, caption: "Here is the summary.", latencyMS: nil)
        hud.update(state: .idle, transcript: nil, caption: nil, latencyMS: nil)
        XCTAssertFalse(hud.pillPanel.isVisible)
        // Pressing the talk key alone keeps it hidden.
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        XCTAssertFalse(hud.pillPanel.isVisible)
        // The new request reaches the Brain: the HUD comes back.
        hud.update(state: .thinking, transcript: "open mail", caption: nil, latencyMS: nil)
        XCTAssertTrue(hud.pillPanel.isVisible)
        XCTAssertFalse(hud.isMinimized)
        XCTAssertFalse(hud.minimizedFlag.get())
        XCTAssertFalse(hud.pillPanel.isKeyWindow)
    }

    func testWakeOnlyRestoreLeavesListening() {
        let hud = HUDController()
        hud.minimize()
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        hud.restore(wakeOnly: true)
        XCTAssertTrue(hud.pillPanel.isVisible)
        XCTAssertEqual(hud.model.state, .idle)
        let screens = NSScreen.screens.map(\.visibleFrame)
        XCTAssertTrue(screens.contains { $0.contains(hud.pillPanel.frame) }, "restored fully on screen")
    }

    func testRevealRules() {
        XCTAssertFalse(HUDController.revealsMinimizedHUD(state: .listening, newRequestSinceMinimize: true))
        XCTAssertFalse(HUDController.revealsMinimizedHUD(state: .idle, newRequestSinceMinimize: true))
        XCTAssertFalse(HUDController.revealsMinimizedHUD(state: nil, newRequestSinceMinimize: true))
        XCTAssertTrue(HUDController.revealsMinimizedHUD(state: .asking, newRequestSinceMinimize: false))
        XCTAssertTrue(HUDController.revealsMinimizedHUD(state: .confirm, newRequestSinceMinimize: false))
        XCTAssertFalse(HUDController.revealsMinimizedHUD(state: .speaking, newRequestSinceMinimize: false))
        XCTAssertTrue(HUDController.revealsMinimizedHUD(state: .thinking, newRequestSinceMinimize: true))
    }

    func testRestoreCornerPlacement() {
        // A 1440x875 visible frame below a 25-pt menu bar.
        let visible = NSRect(x: 0, y: 0, width: 1440, height: 875)
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: visible, frontWindow: nil), .topRight)
        // Full-screen-ish window: both corners covered equally, prefer top-right.
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: visible, frontWindow: visible), .topRight)
        // Window on the right half: top-left covers nothing.
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: visible,
                                                  frontWindow: NSRect(x: 720, y: 0, width: 720, height: 875)), .topLeft)
        // Window on the left half: top-right covers nothing.
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: visible,
                                                  frontWindow: NSRect(x: 0, y: 0, width: 720, height: 875)), .topRight)
        // Small window low on the screen: neither corner covers it, prefer top-right.
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: visible,
                                                  frontWindow: NSRect(x: 1000, y: 0, width: 400, height: 300)), .topRight)
        // A window on a second display to the right, offset frame.
        let second = NSRect(x: 1440, y: 0, width: 1920, height: 1055)
        XCTAssertEqual(HUDRestorePlacement.choose(visibleFrame: second,
                                                  frontWindow: NSRect(x: 2400, y: 200, width: 960, height: 855)), .topLeft)

        // The chosen corner keeps the header fully inside the visible frame with the margin.
        let center = HUDPlacement.topRight.headerCenter(in: visible, size: PillPanel.size, margin: 16)
        let header = NSRect(x: center.x - PillPanel.size.width / 2, y: center.y - PillPanel.size.height / 2,
                            width: PillPanel.size.width, height: PillPanel.size.height)
        XCTAssertTrue(visible.insetBy(dx: 15.9, dy: 15.9).contains(header))
    }

    /// A captured region shows as a thumbnail (scaled down, aspect kept, never enlarged) in place of its text.
    func testSelectionThumbnail() {
        XCTAssertEqual(PillPanel.thumbnailSize(image: NSSize(width: 900, height: 300), maxWidth: 448),
                       NSSize(width: 448, height: 149))
        XCTAssertEqual(PillPanel.thumbnailSize(image: NSSize(width: 300, height: 900), maxWidth: 448),
                       NSSize(width: 50, height: 150))
        XCTAssertEqual(PillPanel.thumbnailSize(image: NSSize(width: 120, height: 40), maxWidth: 448),
                       NSSize(width: 120, height: 40))
        XCTAssertEqual(PillPanel.thumbnailSize(image: .zero, maxWidth: 448), .zero)

        let model = HUDModel()
        model.selectionPreview = "Invoice 42"
        XCTAssertFalse(model.showsSelectionThumbnail, "no image: the text preview stays")
        model.selectionImage = Self.sampleInvoiceImage()
        XCTAssertTrue(model.showsSelectionThumbnail)
        model.caption = "Total is $118."
        XCTAssertFalse(model.showsSelectionThumbnail, "a caption replaces the thumbnail")
    }

    static func sampleInvoiceImage() -> NSImage {
        NSImage(size: NSSize(width: 600, height: 180), flipped: false) { rect in
            NSColor.white.setFill(); rect.fill()
            ("Invoice 42   Due December 1\nTotal $118.00      [ Pay now ]" as NSString).draw(
                at: NSPoint(x: 24, y: 60), withAttributes: [.font: NSFont.systemFont(ofSize: 30), .foregroundColor: NSColor.black])
            return true
        }
    }

    /// Renders the pill for every state to PNGs when HUD_SNAPSHOT_DIR is set (manual visual check).
    func testRenderSnapshots() throws {
        guard let dir = ProcessInfo.processInfo.environment["HUD_SNAPSHOT_DIR"] else { throw XCTSkip("HUD_SNAPSHOT_DIR not set") }
        for state in HUDState.allCases {
            let model = HUDModel()
            model.state = state
            model.transcript = "what is on my screen right now"
            if state == .speaking || state == .confirm {
                model.caption = state == .confirm
                    ? "Send to Sarah Chen: I'll be ten minutes late. Say yes to send."
                    : "Mail, inbox. Newest is from Sarah Chen about lunch tomorrow. Eleven more messages."
            }
            model.latencyMS = state == .idle ? nil : 1630
            if state == .asking { model.caption = "Which Sarah do you mean, Sarah Chen or Sarah Lee?" }
            if state == .asking { model.pendingQuestion = model.caption }
            model.offline = state != .listening
            try render(model, to: URL(fileURLWithPath: dir).appendingPathComponent("pill_\(state.rawValue).png"))
        }
        let answering = HUDModel()
        answering.state = .listening
        answering.pendingQuestion = "Which Sarah do you mean, Sarah Chen or Sarah Lee?"
        try render(answering, to: URL(fileURLWithPath: dir).appendingPathComponent("pill_answering.png"))
        let selection = HUDModel()
        selection.selectionPreview = "Invoice 42 Due December 1 Total $118.00 Pay now"
        try render(selection, to: URL(fileURLWithPath: dir).appendingPathComponent("pill_selection_text.png"))
        selection.selectionImage = Self.sampleInvoiceImage()
        try render(selection, to: URL(fileURLWithPath: dir).appendingPathComponent("pill_selection.png"))
        let typed = TypedPromptPanel()
        typed.configure(heading: "Type a request for Beacon", placeholder: "For example: what's on my screen?")
        let typedView = try XCTUnwrap(typed.contentView)
        typedView.layoutSubtreeIfNeeded()
        let typedBitmap = try XCTUnwrap(typedView.bitmapImageRepForCachingDisplay(in: typedView.bounds))
        typedView.cacheDisplay(in: typedView.bounds, to: typedBitmap)
        try XCTUnwrap(typedBitmap.representation(using: .png, properties: [:]))
            .write(to: URL(fileURLWithPath: dir).appendingPathComponent("typed_prompt.png"))
        let ring = RingView(frame: NSRect(x: 0, y: 0, width: 160, height: 60))
        let rep = try XCTUnwrap(ring.bitmapImageRepForCachingDisplay(in: ring.bounds))
        ring.cacheDisplay(in: ring.bounds, to: rep)
        try XCTUnwrap(rep.representation(using: .png, properties: [:]))
            .write(to: URL(fileURLWithPath: dir).appendingPathComponent("ring.png"))
    }

    private func render(_ model: HUDModel, to url: URL) throws {
        let size = PillPanel.size
        let replyHeight = model.showsSelectionThumbnail
            ? PillPanel.thumbnailReplyHeight(image: model.selectionImage!.size, width: size.width, available: 300)
            : PillPanel.replyHeight(for: model.replyText, width: size.width, available: 300,
                                    question: model.replyLabel != nil)
        let bounds = NSRect(x: 0, y: 0, width: size.width, height: size.height + replyHeight)
        let host = NSHostingView(rootView: PillView(model: model, minimize: {}, turnOff: {}, typePrompt: {}, askAboutRegion: {}, clearSelection: {})
            .frame(width: bounds.width, height: bounds.height))
        let window = OverlayPanel(contentRect: bounds)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: url)
    }
}
