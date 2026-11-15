import CoreGraphics
import XCTest
@testable import BeaconHelper

final class RoleMapTests: XCTestCase {
    func testRoles() {
        XCTAssertEqual(RoleMap.kind(axRole: "AXButton", subrole: nil), .role("button"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXRadioButton", subrole: "AXTabButton"), .role("tab"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXTextField", subrole: "AXSecureTextField"), .role("textfield"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXTable", subrole: nil), .role("list"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXOutline", subrole: nil), .role("list"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXPopUpButton", subrole: nil), .role("popup"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXStaticText", subrole: nil), .role("text"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXWebArea", subrole: nil), .role("group"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXSheet", subrole: nil), .role("window"))
        XCTAssertEqual(RoleMap.kind(axRole: "AXSplitter", subrole: nil), .drop)
        XCTAssertEqual(RoleMap.kind(axRole: "AXScrollArea", subrole: nil), .transparent)
        XCTAssertEqual(RoleMap.kind(axRole: "AXSomethingNew", subrole: nil), .transparent)
    }

    func testAllShortRolesAreInContract() {
        let contract: Set<String> = ["button", "link", "textfield", "textarea", "checkbox", "radio", "menu", "menuitem",
                                     "list", "row", "cell", "tab", "slider", "image", "heading", "text", "toolbar",
                                     "group", "popup", "window"]
        let axRoles = ["AXButton", "AXMenuButton", "AXLink", "AXTextField", "AXComboBox", "AXTextArea", "AXCheckBox",
                       "AXRadioButton", "AXMenu", "AXMenuItem", "AXMenuBarItem", "AXList", "AXRow", "AXCell", "AXTab",
                       "AXSlider", "AXIncrementor", "AXImage", "AXHeading", "AXStaticText", "AXToolbar", "AXGroup",
                       "AXPopUpButton", "AXWindow", "AXDisclosureTriangle", "AXProgressIndicator", "AXScrollArea"]
        for r in axRoles {
            XCTAssertTrue(contract.contains(RoleMap.shortRole(axRole: r, subrole: nil)), r)
        }
    }
}

final class LabelTests: XCTestCase {
    func testLabelPriority() {
        var n = AXNode("AXButton", title: "Send", desc: "Send message")
        XCTAssertEqual(Labels.label(for: n, role: "button")?.0, "Send")
        XCTAssertEqual(Labels.label(for: n, role: "button")?.1, "title")
        n.title = nil
        XCTAssertEqual(Labels.label(for: n, role: "button")?.1, "description")
        n.desc = nil
        n.titleElementText = "Subject:"
        XCTAssertEqual(Labels.label(for: n, role: "button")?.0, "Subject:")
        n.titleElementText = nil
        n.help = "Sends it"
        XCTAssertEqual(Labels.label(for: n, role: "button")?.1, "help")
        n.help = nil
        n.children = [AXNode("AXStaticText", value: .string("Go"))]
        XCTAssertEqual(Labels.label(for: n, role: "button")?.0, "Go")
        XCTAssertEqual(Labels.label(for: n, role: "button")?.1, "child")
    }

    func testSecureValueHidden() {
        var n = AXNode("AXTextField", subrole: "AXSecureTextField", value: .string("hunter2"))
        n.desc = "Password"
        XCTAssertEqual(Labels.value(for: n, role: "textfield"), "(hidden)")
    }

    func testCheckboxValue() {
        let n = AXNode("AXCheckBox", title: "Remember", value: .number(1))
        XCTAssertEqual(Labels.value(for: n, role: "checkbox"), "on")
        XCTAssertTrue(Labels.isChecked(n, role: "checkbox"))
    }

    func testTrim() {
        let long = String(repeating: "a", count: 100)
        let (s, cut) = Labels.trim(long)
        XCTAssertEqual(s.count, 80)
        XCTAssertTrue(cut)
        XCTAssertTrue(s.hasSuffix("..."))
        XCTAssertEqual(Labels.clean("  hello \n  world "), "hello world")
    }

    func testHumanizeIdentifier() {
        XCTAssertEqual(LabelRepair.humanize(identifier: "sendButton"), "send button")
        XCTAssertEqual(LabelRepair.humanize(identifier: "SendButton"), "send button")
        XCTAssertEqual(LabelRepair.humanize(identifier: "URLField"), "url field")
        XCTAssertEqual(LabelRepair.humanize(identifier: "compose_new-message"), "compose new message")
        XCTAssertEqual(LabelRepair.humanize(identifier: "toolbarItem3"), "toolbar item")
        XCTAssertNil(LabelRepair.humanize(identifier: "_NS:123"))
        XCTAssertNil(LabelRepair.humanize(identifier: "12345"))
        XCTAssertNil(LabelRepair.humanize(identifier: "6F9619FF-8B86-D011-B42D-00C04FC964FF"))
        XCTAssertNil(LabelRepair.humanize(identifier: "noteId-AC141637-4608-4550-98B1-51E2B3056EB"))
        XCTAssertNil(LabelRepair.humanize(identifier: ""))
        XCTAssertNil(LabelRepair.humanize(identifier: nil))
    }

    func testNearestLabelled() {
        XCTAssertEqual(LabelRepair.nearestLabelled(in: ["Reply", nil, "Forward"], to: 1), 0)
        XCTAssertEqual(LabelRepair.nearestLabelled(in: [nil, nil, "Forward"], to: 0), 2)
        XCTAssertNil(LabelRepair.nearestLabelled(in: [nil, nil], to: 0))
    }
}

final class SnapshotBuilderTests: XCTestCase {
    func frame(_ y: CGFloat) -> CGRect { CGRect(x: 10, y: y, width: 100, height: 20) }

    func testChoiceDropsOverlappingDuplicateLabelOnly() {
        let labelBounds = CGRect(x: 24, y: 0, width: 69, height: 20)
        let root = AXNode("AXWindow", children: [
            AXNode("AXRadioButton", title: "Saturday", frame: frame(0)),
            AXNode("AXStaticText", value: .string("Saturday"), frame: labelBounds),
            AXNode("AXStaticText", value: .string("Saturday"), frame: frame(80)),
        ])
        let snapshot = SnapshotBuilder.build(root: root, includeRoot: false, maxElements: 20)
        XCTAssertEqual(snapshot.elements.map(\.role), ["radio", "text"])
        XCTAssertEqual(snapshot.elements.last?.frame, frame(80))
    }

    func testMailLikeWindow() {
        var reply = AXNode("AXButton", title: "Reply", frame: frame(0))
        reply.enabled = false
        let unlabeled = AXNode("AXButton", frame: frame(0))
        let toolbar = AXNode("AXToolbar", frame: frame(0), children: [
            AXNode("AXButton", title: "New Message", frame: frame(0)), reply, unlabeled,
        ])
        var search = AXNode("AXTextField", desc: "Search", value: .string(""), frame: frame(40))
        search.focused = true
        let window = AXNode("AXWindow", title: "Inbox", frame: CGRect(x: 0, y: 0, width: 800, height: 600), children: [
            toolbar,
            AXNode("AXGroup", frame: frame(30), children: [search]),  // unlabelled group collapses
            AXNode("AXSplitter", frame: frame(50)),
        ])
        let b = SnapshotBuilder.build(root: window, includeRoot: false, maxElements: 250)
        let text = TextBlock.render(app: "Mail", window: "Inbox", focusedID: b.focusedID, lines: b.lines)
        XCTAssertEqual(text, """
        APP Mail | WINDOW "Inbox" | FOCUS [5]
        [1] toolbar
          [2] button "New Message"
          [3] button "Reply" (disabled)
          [4] button "unlabeled, near 'Reply'" (inferred)
        [5] textfield "Search" value="" (focused)
        """)
        XCTAssertFalse(b.truncated)
        XCTAssertEqual(b.totalElements, 5)
        let json = b.elements[3].json
        XCTAssertEqual(json["label_source"] as? String, "inferred")
        XCTAssertEqual(json["states"] as? [String], ["inferred"])
        XCTAssertEqual(json["bounds"] as? [Int], [10, 0, 100, 20])
        XCTAssertEqual(b.elements[0].json["label_source"] as? String, "none")
        XCTAssertEqual(b.elements[0].json["label"] as? String, "")
        XCTAssertTrue(b.elements[0].json["value"] is NSNull)
    }

    func testIdentifierRepair() {
        let w = AXNode("AXWindow", children: [AXNode("AXButton", identifier: "sendButton", frame: frame(0))])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements[0].label, "send button")
        XCTAssertEqual(b.elements[0].states, ["inferred"])
    }

    func testRowsCappedAt20() {
        let rows = (0..<25).map { i in
            AXNode("AXRow", frame: frame(CGFloat(i * 20)), children: [
                AXNode("AXCell", children: [AXNode("AXStaticText", value: .string("Sender \(i)")),
                                            AXNode("AXStaticText", value: .string("Subject \(i)"))]),
            ])
        }
        var list = AXNode("AXTable", desc: "Messages", frame: frame(0), children: rows)
        list.totalRows = 54
        let w = AXNode("AXWindow", children: [AXNode("AXScrollArea", children: [list])])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        let text = TextBlock.render(app: "Mail", window: "Inbox", focusedID: nil, lines: b.lines)
        let lines = text.split(separator: "\n").map(String.init)
        XCTAssertEqual(lines[0], #"APP Mail | WINDOW "Inbox" | FOCUS none"#)
        XCTAssertEqual(lines[1], #"[1] list "Messages" 54 rows"#)
        XCTAssertEqual(lines[2], #"  [2] row "Sender 0 - Subject 0""#)
        XCTAssertEqual(lines.last, "  ... 34 more rows (scroll or use find)")
        XCTAssertEqual(b.elements.count, 21)
    }

    func testMergeAdjacentText() {
        let w = AXNode("AXWindow", children: [
            AXNode("AXStaticText", value: .string("Hello"), frame: frame(0)),
            AXNode("AXStaticText", value: .string("world"), frame: frame(20)),
            AXNode("AXButton", title: "OK", frame: frame(40)),
            AXNode("AXStaticText", value: .string(String(repeating: "x", count: 90)), frame: frame(60)),
        ])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements.map(\.role), ["text", "button", "text"])
        XCTAssertEqual(b.elements[0].label, "Hello world")
        XCTAssertEqual(b.elements[0].fullText, "Hello world")
        XCTAssertEqual(b.elements[0].frame, CGRect(x: 10, y: 0, width: 100, height: 40))
        XCTAssertEqual(b.elements[2].states, ["truncated"])
        XCTAssertEqual(b.elements[2].label.count, 80)
    }

    func testDropsDecorativeAndZeroSize() {
        let w = AXNode("AXWindow", children: [
            AXNode("AXImage", frame: frame(0)),
            AXNode("AXImage", desc: "Profile photo", frame: frame(0)),
            AXNode("AXButton", title: "Hidden", frame: CGRect(x: 0, y: 0, width: 0, height: 0)),
            AXNode("AXGroup", frame: CGRect.zero, children: [AXNode("AXButton", title: "Inside", frame: frame(0))]),
        ])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements.map(\.label), ["Profile photo", "Inside"])
    }

    func testLabelledGroupKept() {
        let w = AXNode("AXWindow", children: [
            AXNode("AXGroup", title: "Account", children: [AXNode("AXCheckBox", title: "Remember", value: .number(1))]),
        ])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        let text = TextBlock.render(app: "A", window: "W", focusedID: nil, lines: b.lines)
        XCTAssertTrue(text.hasSuffix("[1] group \"Account\"\n  [2] checkbox \"Remember\" value=\"on\" (checked)"), text)
    }

    func testMaxElementsAndDepth() {
        let w = AXNode("AXWindow", children: (0..<10).map { AXNode("AXButton", title: "B\($0)") })
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 4)
        XCTAssertEqual(b.elements.count, 4)
        XCTAssertTrue(b.truncated)
        XCTAssertEqual(b.totalElements, 10)

        var deep = AXNode("AXButton", title: "leaf")
        for i in 0..<30 { deep = AXNode("AXGroup", title: "g\(i)", children: [deep]) }
        let d = SnapshotBuilder.build(root: AXNode("AXWindow", children: [deep]), includeRoot: false, maxElements: 250)
        XCTAssertTrue(d.truncated)
        XCTAssertEqual(d.elements.map(\.depth).max(), 25)
    }

    func testLinkTextFoldedIntoLabel() {
        let w = AXNode("AXWindow", children: [
            AXNode("AXWebArea", title: "Form", children: [
                AXNode("AXLink", children: [AXNode("AXStaticText", value: .string("Sign up"))]),
                AXNode("AXHeading", children: [AXNode("AXStaticText", value: .string("Welcome"))]),
            ]),
        ])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements.map { "\($0.role):\($0.label)" }, ["group:Form", "link:Sign up", "heading:Welcome"])
    }

    func testRowLabelFromCellTitlesAndCellsHidden() {
        let row = AXNode("AXRow", frame: frame(0), children: [
            AXNode("AXCell", desc: "report.pdf", frame: frame(0)),
            AXNode("AXCell", desc: "2 KB", frame: frame(0)),
        ])
        let w = AXNode("AXWindow", children: [AXNode("AXTable", desc: "files", frame: frame(0), children: [row])])
        let b = SnapshotBuilder.build(root: w, includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements.map(\.role), ["list", "row"])
        XCTAssertEqual(b.elements[1].label, "report.pdf - 2 KB")
    }

    func testWindowButtons() {
        var close = AXNode("AXButton", subrole: "AXCloseButton", frame: frame(0))
        close.help = "this button also has an action"
        let b = SnapshotBuilder.build(root: AXNode("AXWindow", children: [close]), includeRoot: false, maxElements: 250)
        XCTAssertEqual(b.elements[0].label, "close window")
    }

    func testQuoteEscaping() {
        XCTAssertEqual(TextBlock.quote(#"say "hi""#), #""say \"hi\"""#)
    }
}

final class ElementTableTests: XCTestCase {
    func testKeepsLastThree() throws {
        let t = ElementTable<String>()
        var ids: [String] = []
        for i in 0..<4 {
            let id = t.nextID()
            t.register(id, [1: "e\(i)"])
            ids.append(id)
        }
        XCTAssertEqual(ids[0], "s_0001")
        XCTAssertEqual(ids[3], "s_0004")
        XCTAssertEqual(try t.resolve(snapshotID: ids[3], elementID: 1), "e3")
        XCTAssertThrowsError(try t.resolve(snapshotID: ids[0], elementID: 1)) { e in
            XCTAssertEqual((e as? HelperError)?.code, .staleSnapshot)
            XCTAssertEqual((e as? HelperError)?.message, "s_0001 expired; take a new snapshot")
        }
        XCTAssertThrowsError(try t.resolve(snapshotID: ids[1], elementID: 9)) { e in
            XCTAssertEqual((e as? HelperError)?.code, .elementNotFound)
        }
    }
}

final class WebClickTests: XCTestCase {
    func testCanClickNeedsSizeAndOnScreenCentre() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        XCTAssertTrue(AccessibilityService.canClick(frame: CGRect(x: 360, y: 422, width: 155, height: 49), screen: screen))
        XCTAssertFalse(AccessibilityService.canClick(frame: CGRect(x: 360, y: 422, width: 0, height: 49), screen: screen))
        XCTAssertFalse(AccessibilityService.canClick(frame: CGRect(x: 360, y: 1200, width: 155, height: 49), screen: screen))
        XCTAssertTrue(AccessibilityService.canClick(frame: CGRect(x: 10, y: 10, width: 5, height: 5), screen: nil))
    }
}

final class TabLabelTests: XCTestCase {
    func testChromeMemoryUsageIsDropped() {
        XCTAssertEqual(Labels.withoutTabNoise("LM Studio Credits - Memory usage - 116 MB"), "LM Studio Credits")
        XCTAssertEqual(Labels.withoutTabNoise("Krillion · the daily dive - High memory usage - 2.0 GB"),
                       "Krillion · the daily dive")
        XCTAssertEqual(Labels.withoutTabNoise("Memory usage notes - Docs"), "Memory usage notes - Docs")
    }
}
