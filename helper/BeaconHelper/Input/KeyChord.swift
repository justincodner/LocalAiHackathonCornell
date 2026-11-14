// Owner: Mac owner (H3). Parses key chords such as `cmd+shift+d`, `cmd+return`, `escape` into a key code + flags.
import Carbon.HIToolbox
import CoreGraphics
import Foundation

struct KeyChord: Equatable {
    let keyCode: CGKeyCode
    let flags: CGEventFlags

    static func == (a: KeyChord, b: KeyChord) -> Bool { a.keyCode == b.keyCode && a.flags.rawValue == b.flags.rawValue }

    static let modifiers: [String: CGEventFlags] = [
        "cmd": .maskCommand, "command": .maskCommand, "⌘": .maskCommand, "meta": .maskCommand, "super": .maskCommand,
        "shift": .maskShift, "⇧": .maskShift,
        "opt": .maskAlternate, "option": .maskAlternate, "alt": .maskAlternate, "⌥": .maskAlternate,
        "ctrl": .maskControl, "control": .maskControl, "⌃": .maskControl,
        "fn": .maskSecondaryFn, "function": .maskSecondaryFn,
    ]

    static let namedKeys: [String: Int] = [
        "return": kVK_Return, "enter": kVK_Return, "ret": kVK_Return, "keypadenter": kVK_ANSI_KeypadEnter,
        "escape": kVK_Escape, "esc": kVK_Escape,
        "tab": kVK_Tab, "space": kVK_Space, "spacebar": kVK_Space,
        "delete": kVK_Delete, "backspace": kVK_Delete,
        "forwarddelete": kVK_ForwardDelete, "fwddelete": kVK_ForwardDelete, "del": kVK_ForwardDelete,
        "up": kVK_UpArrow, "down": kVK_DownArrow, "left": kVK_LeftArrow, "right": kVK_RightArrow,
        "uparrow": kVK_UpArrow, "downarrow": kVK_DownArrow, "leftarrow": kVK_LeftArrow, "rightarrow": kVK_RightArrow,
        "home": kVK_Home, "end": kVK_End, "pageup": kVK_PageUp, "pagedown": kVK_PageDown, "pgup": kVK_PageUp,
        "pgdn": kVK_PageDown, "help": kVK_Help,
        "f1": kVK_F1, "f2": kVK_F2, "f3": kVK_F3, "f4": kVK_F4, "f5": kVK_F5, "f6": kVK_F6,
        "f7": kVK_F7, "f8": kVK_F8, "f9": kVK_F9, "f10": kVK_F10, "f11": kVK_F11, "f12": kVK_F12,
        "minus": kVK_ANSI_Minus, "equal": kVK_ANSI_Equal, "equals": kVK_ANSI_Equal, "comma": kVK_ANSI_Comma,
        "period": kVK_ANSI_Period, "dot": kVK_ANSI_Period, "slash": kVK_ANSI_Slash, "backslash": kVK_ANSI_Backslash,
        "semicolon": kVK_ANSI_Semicolon, "quote": kVK_ANSI_Quote, "apostrophe": kVK_ANSI_Quote,
        "grave": kVK_ANSI_Grave, "backtick": kVK_ANSI_Grave, "leftbracket": kVK_ANSI_LeftBracket,
        "rightbracket": kVK_ANSI_RightBracket,
    ]

    static let characterKeys: [Character: Int] = [
        "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D, "e": kVK_ANSI_E, "f": kVK_ANSI_F,
        "g": kVK_ANSI_G, "h": kVK_ANSI_H, "i": kVK_ANSI_I, "j": kVK_ANSI_J, "k": kVK_ANSI_K, "l": kVK_ANSI_L,
        "m": kVK_ANSI_M, "n": kVK_ANSI_N, "o": kVK_ANSI_O, "p": kVK_ANSI_P, "q": kVK_ANSI_Q, "r": kVK_ANSI_R,
        "s": kVK_ANSI_S, "t": kVK_ANSI_T, "u": kVK_ANSI_U, "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X,
        "y": kVK_ANSI_Y, "z": kVK_ANSI_Z,
        "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3, "4": kVK_ANSI_4,
        "5": kVK_ANSI_5, "6": kVK_ANSI_6, "7": kVK_ANSI_7, "8": kVK_ANSI_8, "9": kVK_ANSI_9,
        "-": kVK_ANSI_Minus, "=": kVK_ANSI_Equal, "[": kVK_ANSI_LeftBracket, "]": kVK_ANSI_RightBracket,
        ";": kVK_ANSI_Semicolon, "'": kVK_ANSI_Quote, ",": kVK_ANSI_Comma, ".": kVK_ANSI_Period,
        "/": kVK_ANSI_Slash, "\\": kVK_ANSI_Backslash, "`": kVK_ANSI_Grave,
    ]

    /// Shifted characters typed as shift + their base key (US layout).
    static let shiftedCharacters: [Character: Character] = [
        "+": "=", "_": "-", "{": "[", "}": "]", ":": ";", "\"": "'", "<": ",", ">": ".", "?": "/", "|": "\\",
        "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    ]

    static let functionKeys: Set<Int> = [kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8, kVK_F9,
                                         kVK_F10, kVK_F11, kVK_F12]

    static func parse(_ raw: String) throws -> KeyChord {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw HelperError(.badRequest, "keys must not be empty") }
        // Split on '+', but a trailing "++" (or the whole string "+") means the plus key itself.
        var parts = text.split(separator: "+", omittingEmptySubsequences: false).map {
            $0.trimmingCharacters(in: .whitespaces).lowercased()
        }
        if text.hasSuffix("+"), parts.count >= 2, parts[parts.count - 1].isEmpty, parts[parts.count - 2].isEmpty {
            parts.removeLast(2)
            parts.append("+")
        } else if text == "+" {
            parts = ["+"]
        }
        guard !parts.contains(where: { $0.isEmpty }) else {
            throw HelperError(.badRequest, "bad key chord '\(raw)'; use names like cmd+shift+d, cmd+return, escape")
        }
        var flags: CGEventFlags = []
        var key: (Int, CGEventFlags)?
        for p in parts {
            if let m = modifiers[p] {
                flags.insert(m)
                continue
            }
            guard key == nil else {
                throw HelperError(.badRequest, "bad key chord '\(raw)': more than one non-modifier key")
            }
            guard let k = keyCode(for: p) else {
                throw HelperError(.badRequest, "unknown key '\(p)' in '\(raw)'")
            }
            key = k
        }
        guard let (code, extra) = key else {
            throw HelperError(.badRequest, "bad key chord '\(raw)': no key besides modifiers")
        }
        flags.formUnion(extra)
        // Arrow and navigation keys carry these flags when typed on a real keyboard.
        if [kVK_UpArrow, kVK_DownArrow, kVK_LeftArrow, kVK_RightArrow].contains(code) {
            flags.insert(.maskNumericPad)
            flags.insert(.maskSecondaryFn)
        } else if [kVK_Home, kVK_End, kVK_PageUp, kVK_PageDown, kVK_ForwardDelete, kVK_Help].contains(code)
                    || functionKeys.contains(code) {
            flags.insert(.maskSecondaryFn)
        }
        return KeyChord(keyCode: CGKeyCode(code), flags: flags)
    }

    private static func keyCode(for p: String) -> (Int, CGEventFlags)? {
        if let n = namedKeys[p] { return (n, []) }
        if p == "plus" { return (kVK_ANSI_Equal, .maskShift) }
        guard p.count == 1, let ch = p.first else { return nil }
        if let n = characterKeys[ch] { return (n, []) }
        if let base = shiftedCharacters[ch], let n = characterKeys[base] { return (n, .maskShift) }
        return nil
    }
}
