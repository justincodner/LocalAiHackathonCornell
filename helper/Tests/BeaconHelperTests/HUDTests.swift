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
            XCTAssertTrue(panel.ignoresMouseEvents)
            XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
            XCTAssertFalse(panel.hidesOnDeactivate)
            XCTAssertTrue(panel.collectionBehavior.contains(.canJoinAllSpaces))
        }
        XCTAssertEqual(hud.pillPanel.frame.size, PillPanel.size)
        XCTAssertFalse(hud.pillPanel.isKeyWindow)
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
            let renderer = ImageRenderer(content: PillView(model: model).frame(width: 520, height: 72))
            renderer.scale = 2
            let image = try XCTUnwrap(renderer.nsImage)
            let tiff = try XCTUnwrap(image.tiffRepresentation)
            let png = try XCTUnwrap(NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]))
            try png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("pill_\(state.rawValue).png"))
        }
        let ring = RingView(frame: NSRect(x: 0, y: 0, width: 160, height: 60))
        let rep = try XCTUnwrap(ring.bitmapImageRepForCachingDisplay(in: ring.bounds))
        ring.cacheDisplay(in: ring.bounds, to: rep)
        try XCTUnwrap(rep.representation(using: .png, properties: [:]))
            .write(to: URL(fileURLWithPath: dir).appendingPathComponent("ring.png"))
    }
}
