// Owner: Mac owner. Typed requests and "ask about part of the screen": shortcut matching, selection geometry,
// the focus payload, and how the HUD and EventHub treat a pending selection.
import AppKit
import Carbon.HIToolbox
import XCTest
@testable import BeaconHelper

final class ShortcutTests: XCTestCase {
    func testParsesTheDefaultShortcuts() throws {
        let type = try XCTUnwrap(GlobalShortcut.parse("ctrl+opt+space"))
        XCTAssertEqual(type.keyCode, UInt16(kVK_Space))
        XCTAssertEqual(type.modifiers, [.control, .option])
        XCTAssertEqual(type.spokenName, "Control Option Space")
        XCTAssertEqual(type.carbonModifiers, UInt32(controlKey | optionKey))

        let snap = try XCTUnwrap(GlobalShortcut.parse("ctrl+opt+s"))
        XCTAssertEqual(snap.keyCode, UInt16(kVK_ANSI_S))
        XCTAssertEqual(snap.spokenName, "Control Option S")
        XCTAssertNotEqual(type, snap)
    }

    func testMatchesExactModifiersOnly() throws {
        let shortcut = try XCTUnwrap(GlobalShortcut.parse("ctrl+opt+space"))
        let space = UInt16(kVK_Space)
        XCTAssertTrue(shortcut.matches(keyCode: space, flags: [.control, .option]))
        XCTAssertTrue(shortcut.matches(keyCode: space, flags: [.control, .option, .capsLock, .function]),
                      "caps lock and fn are ignored")
        XCTAssertFalse(shortcut.matches(keyCode: space, flags: [.control, .option, .shift]))
        XCTAssertFalse(shortcut.matches(keyCode: space, flags: [.option]), "Option+Space types a character")
        XCTAssertFalse(shortcut.matches(keyCode: space, flags: [.control]))
        XCTAssertFalse(shortcut.matches(keyCode: UInt16(kVK_ANSI_S), flags: [.control, .option]))
    }

    func testRejectsShortcutsThatWouldStealTyping() {
        XCTAssertNil(GlobalShortcut.parse("space"))
        XCTAssertNil(GlobalShortcut.parse("shift+s"))
        XCTAssertNil(GlobalShortcut.parse("ctrl+opt"))
        XCTAssertNil(GlobalShortcut.parse("ctrl+opt+nosuchkey"))
        XCTAssertNotNil(GlobalShortcut.parse("cmd+shift+k"))
    }

    func testOptionInsideAControlShortcutDoesNotStartTalking() {
        XCTAssertTrue(HotkeyMonitor.startsTalk(flags: [.option], talkFlag: .option))
        XCTAssertTrue(HotkeyMonitor.startsTalk(flags: [.option, .capsLock], talkFlag: .option))
        XCTAssertFalse(HotkeyMonitor.startsTalk(flags: [.option, .control], talkFlag: .option))
        XCTAssertFalse(HotkeyMonitor.startsTalk(flags: [.option, .command], talkFlag: .option))
        XCTAssertFalse(HotkeyMonitor.startsTalk(flags: [.option, .shift], talkFlag: .option))
        XCTAssertTrue(HotkeyMonitor.startsTalk(flags: [.command], talkFlag: .command))
    }
}

final class RegionGeometryTests: XCTestCase {
    func testDragInAnyDirectionGivesTheSameRect() {
        let expected = CGRect(x: 10, y: 20, width: 90, height: 60)
        XCTAssertEqual(RegionGeometry.rect(from: CGPoint(x: 10, y: 20), to: CGPoint(x: 100, y: 80)), expected)
        XCTAssertEqual(RegionGeometry.rect(from: CGPoint(x: 100, y: 80), to: CGPoint(x: 10, y: 20)), expected)
        XCTAssertEqual(RegionGeometry.rect(from: CGPoint(x: 10, y: 80), to: CGPoint(x: 100, y: 20)), expected)
        XCTAssertTrue(RegionGeometry.isTooSmall(RegionGeometry.rect(from: .zero, to: CGPoint(x: 3, y: 200))))
        XCTAssertFalse(RegionGeometry.isTooSmall(expected))
    }

    func testFlipsAppKitToTopLeftCoordinates() {
        // Primary screen 900 pt tall. A rect 100 pt above the bottom edge, 50 pt tall.
        let flipped = RegionGeometry.topLeftRect(fromAppKit: CGRect(x: 40, y: 100, width: 200, height: 50),
                                                 primaryScreenHeight: 900)
        XCTAssertEqual(flipped, CGRect(x: 40, y: 750, width: 200, height: 50))
        // A display above the primary one has negative AppKit-flipped y.
        let above = RegionGeometry.topLeftRect(fromAppKit: CGRect(x: 0, y: 1000, width: 100, height: 100),
                                               primaryScreenHeight: 900)
        XCTAssertEqual(above.minY, -200)
        // The flip is its own inverse, and matches the highlight ring's conversion.
        XCTAssertEqual(RegionGeometry.topLeftRect(fromAppKit: flipped, primaryScreenHeight: 900),
                       CGRect(x: 40, y: 100, width: 200, height: 50))
        XCTAssertEqual(appKitRect(fromAXRect: flipped, primaryScreenHeight: 900), CGRect(x: 40, y: 100, width: 200, height: 50))
    }

    func testPicksTheDisplayShowingMostOfTheSelectionAndClipsToIt() throws {
        let primary = RegionGeometry.Display(id: 1, frame: CGRect(x: 0, y: 0, width: 1440, height: 900))
        let left = RegionGeometry.Display(id: 2, frame: CGRect(x: -1920, y: -180, width: 1920, height: 1080))
        let displays = [primary, left]

        let onPrimary = try XCTUnwrap(RegionGeometry.captureTarget(for: CGRect(x: 100, y: 100, width: 300, height: 200),
                                                                   displays: displays))
        XCTAssertEqual(onPrimary.display, primary)
        XCTAssertEqual(onPrimary.local, CGRect(x: 100, y: 100, width: 300, height: 200))

        // Mostly on the left display: local coordinates are relative to that display's top-left corner.
        let spanning = try XCTUnwrap(RegionGeometry.captureTarget(for: CGRect(x: -400, y: 0, width: 500, height: 100),
                                                                  displays: displays))
        XCTAssertEqual(spanning.display, left)
        XCTAssertEqual(spanning.global, CGRect(x: -400, y: 0, width: 400, height: 100))
        XCTAssertEqual(spanning.local, CGRect(x: 1520, y: 180, width: 400, height: 100))

        XCTAssertNil(RegionGeometry.captureTarget(for: CGRect(x: 5000, y: 5000, width: 50, height: 50), displays: displays))
    }
}

final class SelectionFocusTests: XCTestCase {
    func testOCRLinesAreInReadingOrderAndRowsStayTogether() {
        // Vision boxes: normalised, bottom-left origin (higher y is nearer the top).
        let lines = [
            SelectionFocus.OCRLine(text: "Total", box: CGRect(x: 0.05, y: 0.10, width: 0.2, height: 0.1)),
            SelectionFocus.OCRLine(text: "Invoice 42", box: CGRect(x: 0.05, y: 0.80, width: 0.5, height: 0.1)),
            SelectionFocus.OCRLine(text: "$118.00", box: CGRect(x: 0.70, y: 0.11, width: 0.2, height: 0.1)),
            SelectionFocus.OCRLine(text: "  ", box: CGRect(x: 0.05, y: 0.50, width: 0.2, height: 0.1)),
        ]
        XCTAssertEqual(SelectionFocus.readingOrder(lines), "Invoice 42\nTotal  $118.00")
    }

    func testElementLinesKeepOnlyElementsInsideTheSelection() {
        let elements: [JSONObject] = [
            ["role": "button", "label": "Send", "value": NSNull(), "bounds": [110, 110, 60, 20]],
            ["role": "textfield", "label": "To", "value": "sarah@example.com", "bounds": [110, 140, 200, 20]],
            ["role": "button", "label": "Outside", "bounds": [900, 900, 60, 20]],
            ["role": "text", "label": "Half in", "bounds": [380, 110, 100, 20]],   // 20 % inside
            ["role": "group", "label": "", "bounds": [100, 100, 50, 50]],
            ["role": "button", "label": "Send", "bounds": [120, 120, 60, 20]],     // duplicate
        ]
        let lines = SelectionFocus.elementLines(elements, region: CGRect(x: 100, y: 100, width: 300, height: 100))
        XCTAssertEqual(lines, ["button Send", "textfield To: sarah@example.com"])
    }

    func testTextCombinesOCRWithControlsOCRMissed() {
        let text = SelectionFocus.text(ocr: "Send\nInvoice 42", elementLines: ["button Send", "checkbox Paid: on"])
        XCTAssertEqual(text, "Send\nInvoice 42\n\nControls in the selection:\n- checkbox Paid: on")
        XCTAssertEqual(SelectionFocus.text(ocr: "", elementLines: ["image Company logo"]), "- image Company logo")
        let long = SelectionFocus.text(ocr: String(repeating: "a", count: 5000), elementLines: [], limit: 100)
        XCTAssertEqual(long.count, 100)
        XCTAssertTrue(long.hasSuffix("…"))
    }

    func testPayloadShape() throws {
        XCTAssertNil(SelectionFocus.payload(ocr: "  ", elementLines: [], app: "Safari", bounds: .zero))
        let focus = try XCTUnwrap(SelectionFocus.payload(ocr: "Total $118.00", elementLines: [], app: "Safari",
                                                         bounds: CGRect(x: 10.4, y: 20.6, width: 300, height: 99.5)))
        XCTAssertEqual(focus["text"] as? String, "Total $118.00")
        XCTAssertEqual(focus["source"] as? String, "selection")
        XCTAssertEqual(focus["app"] as? String, "Safari")
        XCTAssertEqual(focus["bounds"] as? [Int], [10, 21, 300, 100])
        XCTAssertNotNil(JSON.string(focus), "the payload must serialise into the event")
        XCTAssertEqual(SelectionFocus.preview("one\ntwo"), "one two")
        XCTAssertEqual(SelectionFocus.preview(String(repeating: "x", count: 300), limit: 10).count, 10)
    }

    func testPendingFocusRidesOnTheNextUtteranceOnly() throws {
        let hub = EventHub()
        let focus: JSONObject = ["text": "Total $118.00", "source": "selection"]
        let id = hub.setPendingFocus(focus)
        XCTAssertTrue(hub.hasPendingFocus)
        XCTAssertFalse(hub.clearPendingFocus(id: id + 1), "a stale timer must not drop a newer selection")
        hub.emit("listen.started")
        hub.emit("stt.final", ["utterance_id": "u_0001", "text": "what is the total?"])
        hub.emit("stt.final", ["utterance_id": "u_0002", "text": "and the date?"])
        let finals = hub.recent.filter { $0["type"] as? String == "stt.final" }
        XCTAssertEqual((finals[0]["focus"] as? JSONObject)?["text"] as? String, "Total $118.00")
        XCTAssertNil(finals[1]["focus"])
        XCTAssertFalse(hub.hasPendingFocus)
        XCTAssertFalse(hub.clearPendingFocus(id: id))
    }

    func testHUDMoveCommandDoesNotConsumeTheSelection() {
        let hub = EventHub(moveHUD: { _ in })
        hub.setPendingFocus(["text": "x", "source": "selection"])
        hub.emit("stt.final", ["text": "Move Beacon to the top left."])
        XCTAssertTrue(hub.hasPendingFocus)
        XCTAssertTrue(hub.recent.isEmpty)
    }
}

@MainActor
final class PromptHUDTests: XCTestCase {
    func testSelectionHeaderAndReply() {
        let model = HUDModel()
        model.selectionPreview = "Invoice 42 Total $118.00"
        XCTAssertTrue(model.showsSelection)
        XCTAssertEqual(model.statusText.title, "Ask about this selection")
        XCTAssertEqual(model.statusText.detail, "Hold Option to ask, or type it")
        XCTAssertEqual(model.replyText, "Invoice 42 Total $118.00")
        XCTAssertEqual(model.replyLabel, "Selection")

        model.state = .listening
        XCTAssertEqual(model.statusText.title, "Asking about your selection")
        XCTAssertEqual(model.statusText.detail, "Say your question, then release Option")
        model.transcript = "what is the total"
        XCTAssertEqual(model.statusText.accessibilityLabel, "Asking about your selection, You said: what is the total")

        model.state = .thinking
        XCTAssertFalse(model.showsSelection, "once the question is in, the normal thinking look returns")
        XCTAssertNil(model.replyLabel)
    }

    @MainActor
    func testRemovingTheScreenshotClearsThumbnailAndText() {
        let hud = HUDController()
        let image = HUDTests.sampleInvoiceImage().cgImage(forProposedRect: nil, context: nil, hints: nil)
        hud.setSelection(preview: "Invoice 42", image: image)
        XCTAssertTrue(hud.model.showsSelectionThumbnail)
        var removed = false
        hud.clearSelection = { removed = true; hud.setSelection(preview: nil) }
        hud.clearSelection()
        XCTAssertTrue(removed)
        XCTAssertNil(hud.model.selectionImage)
        XCTAssertFalse(hud.model.showsSelection)
        XCTAssertEqual(hud.model.replyText, "")
    }

        func testQuestionModelStateParsing() {
        XCTAssertEqual(QuestionModelState(json: true), .on)
        XCTAssertEqual(QuestionModelState(json: false), .off)
        XCTAssertEqual(QuestionModelState(json: "loading"), .loading)
        XCTAssertNil(QuestionModelState(json: nil))
        XCTAssertNil(QuestionModelState(json: 3))
        XCTAssertEqual(PillView.questionModelTitle(.loading), "Loading 4B model…")
        XCTAssertEqual(PillView.questionModelTitle(.off, memoryBytes: 8 << 30), "Use 4B model for questions (slower on 8 GB)")
        XCTAssertEqual(PillView.questionModelTitle(.on, memoryBytes: 12 << 30), "Use 4B model for questions (slower on 12 GB)")
        XCTAssertEqual(PillView.questionModelTitle(.off, memoryBytes: 16 << 30), "Use 4B model for questions")
        XCTAssertEqual(PillView.questionModelTitle(.off, memoryBytes: 64 << 30), "Use 4B model for questions")
    }

    @MainActor
    func testQuestionModelToggleShowsLoadingAndIgnoresClicksWhileLoading() {
        let hud = HUDController()
        var requests: [Bool] = []
        hud.toggleQuestionModel = { requests.append($0) }
        hud.requestQuestionModelToggle()
        XCTAssertEqual(requests, [true])
        XCTAssertEqual(hud.model.questionModel, .loading)
        hud.requestQuestionModelToggle()
        XCTAssertEqual(requests, [true], "a second click while the 4B loads does nothing")
        hud.setQuestionModel(.on)
        hud.requestQuestionModelToggle()
        XCTAssertEqual(requests, [true, false])
    }

    @MainActor
    func testSelectionShowsTheScreenshotWithASpinnerWhileItIsRead() {
        let hud = HUDController()
        let image = HUDTests.sampleInvoiceImage().cgImage(forProposedRect: nil, context: nil, hints: nil)
        hud.setSelection(preview: PromptCoordinator.loadingText, image: image, loading: true)
        XCTAssertTrue(hud.model.selectionLoading)
        XCTAssertTrue(hud.model.showsSelectionThumbnail, "the screenshot shows while it is read")
        XCTAssertEqual(hud.model.statusText.title, "Reading your selection")
        hud.setSelection(preview: "Invoice 42", image: image)
        XCTAssertFalse(hud.model.selectionLoading)
        XCTAssertEqual(hud.model.statusText.title, "Ask about this selection")
        hud.setSelection(preview: nil)
        XCTAssertFalse(hud.model.selectionLoading)
    }

    func testSelectionOverridesAnOlderQuestion() {
        let model = HUDModel()
        model.state = .asking
        model.pendingQuestion = "Which Sarah?"
        model.selectionPreview = "Some text"
        XCTAssertEqual(model.statusText.title, "Ask about this selection")
        model.state = .listening
        XCTAssertFalse(model.isAnswering)
    }

    func testControllerShowsAndClearsTheSelection() {
        let hud = HUDController()
        hud.update(state: .speaking, transcript: nil, caption: "Earlier reply", latencyMS: nil)
        hud.setSelection(preview: "Selected text")
        XCTAssertEqual(hud.model.state, .idle)
        XCTAssertEqual(hud.model.replyText, "Selected text")
        hud.speechStarted(id: "sp_1", text: "Selection ready.")
        XCTAssertEqual(hud.model.replyText, "Selected text", "the spoken prompt does not hide the selection")
        hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil)
        hud.listenCancelled()
        XCTAssertTrue(hud.model.showsSelection, "a too-short recording keeps the selection")
        hud.setSelection(preview: nil)
        XCTAssertFalse(hud.model.showsSelection)
    }

    func testTypedPromptPanelTakesKeyboardFocusWithoutActivating() {
        let panel = TypedPromptPanel()
        XCTAssertTrue(panel.canBecomeKey)
        XCTAssertFalse(panel.canBecomeMain)
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
        panel.configure(heading: "Answer Beacon's question", placeholder: "Which Sarah?")
        XCTAssertEqual(panel.fieldAccessibilityLabel, "Answer Beacon's question")
        panel.configure(heading: "Ask Beacon a question", placeholder: "Ask Beacon a question")
        XCTAssertEqual(panel.placeholder, "Ask Beacon a question")
        let selection = SelectionPanel(screen: NSScreen.screens[0])
        XCTAssertTrue(selection.canBecomeKey)
        XCTAssertTrue(selection.styleMask.contains(.nonactivatingPanel))
    }

    @MainActor
    func testTypedPromptIsAttachedToTheHUDSoTheyMoveTogether() {
        let hud = HUDController()
        let panel = TypedPromptPanel()
        panel.present(attachedTo: hud.pillPanel, heading: "Ask Beacon a question", placeholder: "Ask Beacon a question")
        XCTAssertTrue(panel.parent === hud.pillPanel)
        let before = panel.frame.origin
        hud.pillPanel.setFrameOrigin(NSPoint(x: hud.pillPanel.frame.minX + 40, y: hud.pillPanel.frame.minY - 30))
        XCTAssertEqual(panel.frame.minX, before.x + 40, accuracy: 0.5)
        XCTAssertEqual(panel.frame.minY, before.y - 30, accuracy: 0.5)
        panel.cancel()
        XCTAssertNil(panel.parent, "closing detaches it")
    }

    func testTypedPromptSitsBelowTheHUDOrAboveItNearTheBottom() {
        let screen = NSRect(x: 0, y: 0, width: 1440, height: 875)
        let top = TypedPromptPanel.frame(near: NSRect(x: 900, y: 780, width: 480, height: 58), screens: [screen])
        XCTAssertEqual(top.maxY, 780 - TypedPromptPanel.gap)
        XCTAssertEqual(top.width, 480)
        let bottom = TypedPromptPanel.frame(near: NSRect(x: 480, y: 16, width: 480, height: 58), screens: [screen])
        XCTAssertEqual(bottom.minY, 16 + 58 + TypedPromptPanel.gap)
        XCTAssertTrue(screen.contains(bottom))
    }
}

final class OCRTests: XCTestCase {
    /// Runs the real on-device Vision request on rendered text (no screen capture, no network).
    func testRecognizesRenderedTextInReadingOrder() async throws {
        let size = NSSize(width: 520, height: 160)
        let rep = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width) * 2,
                                                 pixelsHigh: Int(size.height) * 2, bitsPerSample: 8,
                                                 samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                                 colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        rep.size = size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        NSColor.white.setFill()
        NSRect(origin: .zero, size: size).fill()
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 28), .foregroundColor: NSColor.black]
        ("Invoice 42" as NSString).draw(at: NSPoint(x: 20, y: 100), withAttributes: attrs)
        ("Total due 118 dollars" as NSString).draw(at: NSPoint(x: 20, y: 30), withAttributes: attrs)
        NSGraphicsContext.restoreGraphicsState()
        let image = try XCTUnwrap(rep.cgImage)

        let text = SelectionFocus.readingOrder(try await RegionReader.recognize(image))
        let lines = text.split(separator: "\n").map(String.init)
        XCTAssertEqual(lines.count, 2, text)
        XCTAssertTrue(lines.first?.contains("Invoice 42") == true, text)
        XCTAssertTrue(lines.last?.contains("Total due 118") == true, text)
    }
}
