// Owner: Mac owner (H3). CGEvent synthesis: clicks, key chords, unicode typing, scroll wheel.
// Coordinates are global screen points with a top-left origin (the same space as AX positions).
import CoreGraphics
import Foundation

enum SyntheticEvents {
    private static func source() -> CGEventSource? { CGEventSource(stateID: .hidSystemState) }

    static func pause(_ ms: Int) { usleep(useconds_t(ms * 1000)) }

    static func moveMouse(to p: CGPoint) {
        CGEvent(mouseEventSource: source(), mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?
            .post(tap: .cghidEventTap)
    }

    static func click(at p: CGPoint, right: Bool = false) throws {
        let (down, up, button): (CGEventType, CGEventType, CGMouseButton) =
            right ? (.rightMouseDown, .rightMouseUp, .right) : (.leftMouseDown, .leftMouseUp, .left)
        guard let d = CGEvent(mouseEventSource: source(), mouseType: down, mouseCursorPosition: p, mouseButton: button),
              let u = CGEvent(mouseEventSource: source(), mouseType: up, mouseCursorPosition: p, mouseButton: button) else {
            throw HelperError(.actionFailed, "could not create a mouse event")
        }
        moveMouse(to: p)
        pause(15)
        d.flags = []
        u.flags = []
        d.post(tap: .cghidEventTap)
        pause(30)
        u.post(tap: .cghidEventTap)
    }

    static func key(code: CGKeyCode, flags: CGEventFlags) throws {
        guard let d = CGEvent(keyboardEventSource: source(), virtualKey: code, keyDown: true),
              let u = CGEvent(keyboardEventSource: source(), virtualKey: code, keyDown: false) else {
            throw HelperError(.actionFailed, "could not create a key event")
        }
        d.flags = flags
        u.flags = flags
        d.post(tap: .cghidEventTap)
        pause(8)
        u.post(tap: .cghidEventTap)
    }

    /// Types one chunk (at most 20 UTF-16 units, per CGEvent limits) via keyboardSetUnicodeString.
    static func typeChunk(_ utf16: [UniChar]) throws {
        guard let d = CGEvent(keyboardEventSource: source(), virtualKey: 0, keyDown: true),
              let u = CGEvent(keyboardEventSource: source(), virtualKey: 0, keyDown: false) else {
            throw HelperError(.actionFailed, "could not create a key event")
        }
        // Explicitly clear modifiers so a held push-to-talk key does not turn text into shortcuts.
        d.flags = []
        u.flags = []
        utf16.withUnsafeBufferPointer { buf in
            d.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
            u.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
        }
        d.post(tap: .cghidEventTap)
        pause(4)
        u.post(tap: .cghidEventTap)
    }

    /// Pixel scroll. Positive `dy` scrolls up (towards the top of the content), positive `dx` scrolls left.
    static func scroll(dx: Int32, dy: Int32, at point: CGPoint?) throws {
        guard let e = CGEvent(scrollWheelEvent2Source: source(), units: .pixel, wheelCount: 2,
                              wheel1: dy, wheel2: dx, wheel3: 0) else {
            throw HelperError(.actionFailed, "could not create a scroll event")
        }
        if let point { e.location = point }
        e.flags = []
        e.post(tap: .cghidEventTap)
    }
}
