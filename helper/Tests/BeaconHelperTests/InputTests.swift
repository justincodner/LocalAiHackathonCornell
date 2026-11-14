import Carbon.HIToolbox
import CoreGraphics
import XCTest
@testable import BeaconHelper

final class KeyChordTests: XCTestCase {
    func code(_ s: String) throws -> Int { Int(try KeyChord.parse(s).keyCode) }

    func testChords() throws {
        let d = try KeyChord.parse("cmd+shift+d")
        XCTAssertEqual(Int(d.keyCode), kVK_ANSI_D)
        XCTAssertTrue(d.flags.contains(.maskCommand))
        XCTAssertTrue(d.flags.contains(.maskShift))
        XCTAssertFalse(d.flags.contains(.maskAlternate))

        let r = try KeyChord.parse("cmd+return")
        XCTAssertEqual(Int(r.keyCode), kVK_Return)
        XCTAssertEqual(r.flags, .maskCommand)

        XCTAssertEqual(try code("escape"), kVK_Escape)
        XCTAssertEqual(try code("esc"), kVK_Escape)
        XCTAssertEqual(try code("Tab"), kVK_Tab)
        XCTAssertEqual(try code("enter"), kVK_Return)
        XCTAssertEqual(try code("backspace"), kVK_Delete)
        XCTAssertEqual(try code("forwarddelete"), kVK_ForwardDelete)
        XCTAssertEqual(try code("pagedown"), kVK_PageDown)
        XCTAssertEqual(try code("f5"), kVK_F5)
        XCTAssertEqual(try code("f12"), kVK_F12)
        XCTAssertEqual(try code("7"), kVK_ANSI_7)
        XCTAssertEqual(try code("cmd+,"), kVK_ANSI_Comma)
        XCTAssertEqual(try code("ctrl+option+space"), kVK_Space)
        XCTAssertEqual(try code("command + q"), kVK_ANSI_Q)

        let plus = try KeyChord.parse("cmd++")
        XCTAssertEqual(Int(plus.keyCode), kVK_ANSI_Equal)
        XCTAssertTrue(plus.flags.contains(.maskShift))

        let up = try KeyChord.parse("alt+up")
        XCTAssertEqual(Int(up.keyCode), kVK_UpArrow)
        XCTAssertTrue(up.flags.contains(.maskAlternate))
        XCTAssertTrue(try KeyChord.parse("fn+f1").flags.contains(.maskSecondaryFn))
    }

    func testBadChords() {
        for bad in ["", "cmd", "cmd+shift", "cmd+a+b", "hyper+x", "cmd+", "banana"] {
            XCTAssertThrowsError(try KeyChord.parse(bad), bad) { e in
                XCTAssertEqual((e as? HelperError)?.code, .badRequest, bad)
            }
        }
    }
}

final class InputLogicTests: XCTestCase {
    func testChunks() {
        let text = String(repeating: "a", count: 45)
        XCTAssertEqual(InputService.chunks(text).map(\.count), [20, 20, 5])
        // Emoji are two UTF-16 units and must never be split.
        let emoji = String(repeating: "😀", count: 11)
        let c = InputService.chunks(emoji)
        XCTAssertEqual(c.map(\.count), [20, 2])
        XCTAssertEqual(c.map { String(utf16CodeUnits: $0, count: $0.count) }.joined(), emoji)
        XCTAssertTrue(InputService.chunks("").isEmpty)
    }

    func testScrollDelta() throws {
        let page = CGSize(width: 1000, height: 800)
        XCTAssertEqual(try InputService.scrollDelta(direction: "down", amount: "page", page: page).dy, -680)
        XCTAssertEqual(try InputService.scrollDelta(direction: "up", amount: "300", page: page).dy, 300)
        XCTAssertEqual(try InputService.scrollDelta(direction: "right", amount: "page", page: page).dx, -850)
        XCTAssertThrowsError(try InputService.scrollDelta(direction: "down", amount: "lots", page: page))
        XCTAssertThrowsError(try InputService.scrollDelta(direction: "down", amount: "-5", page: page))
    }
}

final class AppMatcherTests: XCTestCase {
    let apps: [AppMatcher.Candidate] = [
        .init(name: "Notes", running: true), .init(name: "Mail", running: true), .init(name: "Safari", running: false),
        .init(name: "System Settings", running: false), .init(name: "Visual Studio Code", running: false),
        .init(name: "Notion", running: false), .init(name: "Google Chrome", running: false),
        .init(name: "Calculator", running: false), .init(name: "Calendar", running: false),
    ]

    func best(_ q: String) -> String? { AppMatcher.match(q, in: apps).best?.name }

    func testMatches() {
        XCTAssertEqual(best("notes"), "Notes")
        XCTAssertEqual(best("Notes app"), "Notes")
        XCTAssertEqual(best("the mail app"), "Mail")
        XCTAssertEqual(best("safary"), "Safari")
        XCTAssertEqual(best("system preferences"), "System Settings")
        XCTAssertEqual(best("settings"), "System Settings")
        XCTAssertEqual(best("chrome"), "Google Chrome")
        XCTAssertEqual(best("calc"), "Calculator")
        XCTAssertEqual(best("calendar"), "Calendar")
        XCTAssertEqual(best("Safari.app"), "Safari")
    }

    func testNoMatchGivesCloseMatches() {
        let r = AppMatcher.match("notez pad", in: apps)
        XCTAssertNil(r.best)
        XCTAssertTrue(r.close.contains("Notes"), "\(r.close)")
        XCTAssertNil(AppMatcher.match("xylophone", in: apps).best)
    }
}
