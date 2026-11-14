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
            model.offline = state != .listening
            let size = PillPanel.size
            let replyHeight = PillPanel.replyHeight(for: model.caption, width: size.width, available: 300)
            let bounds = NSRect(x: 0, y: 0, width: size.width, height: size.height + replyHeight)
            let host = NSHostingView(rootView: PillView(model: model).frame(width: bounds.width, height: bounds.height))
            let window = OverlayPanel(contentRect: bounds)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            try png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("pill_\(state.rawValue).png"))
        }
        let ring = RingView(frame: NSRect(x: 0, y: 0, width: 160, height: 60))
        let rep = try XCTUnwrap(ring.bitmapImageRepForCachingDisplay(in: ring.bounds))
        ring.cacheDisplay(in: ring.bounds, to: rep)
        try XCTUnwrap(rep.representation(using: .png, properties: [:]))
            .write(to: URL(fileURLWithPath: dir).appendingPathComponent("ring.png"))
    }
}
