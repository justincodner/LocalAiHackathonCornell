// Owner: Mac owner. System-wide shortcuts: Control+Option+Space (type a request) and Control+Option+S
// (ask about part of the screen). Registered with Carbon's RegisterEventHotKey, which needs no permission and
// swallows the keystroke so it never reaches the frontmost app. If another app already owns the combination,
// a global key-down monitor (Accessibility) is used instead; that one cannot swallow the key.
import AppKit
import Carbon.HIToolbox

/// A key plus modifiers, matched exactly (extra modifiers do not match). Pure, so it is unit tested.
struct GlobalShortcut: Equatable {
    let keyCode: UInt16
    let modifiers: NSEvent.ModifierFlags
    /// How the shortcut is spoken and shown, e.g. "Control Option Space".
    let spokenName: String

    static func == (a: GlobalShortcut, b: GlobalShortcut) -> Bool {
        a.keyCode == b.keyCode && a.modifiers.rawValue == b.modifiers.rawValue
    }

    /// Only these modifiers take part in matching; caps lock, fn and the numeric-pad bit are ignored.
    static let relevant: NSEvent.ModifierFlags = [.command, .option, .control, .shift]

    /// Parses `ctrl+opt+space` style text (the KeyChord syntax). Nil for an unknown key or a bare modifier.
    static func parse(_ text: String) -> GlobalShortcut? {
        guard let chord = try? KeyChord.parse(text) else { return nil }
        var mods: NSEvent.ModifierFlags = []
        if chord.flags.contains(.maskControl) { mods.insert(.control) }
        if chord.flags.contains(.maskAlternate) { mods.insert(.option) }
        if chord.flags.contains(.maskShift) { mods.insert(.shift) }
        if chord.flags.contains(.maskCommand) { mods.insert(.command) }
        // A shortcut without Control, Option or Command would steal ordinary typing.
        guard !mods.intersection([.control, .option, .command]).isEmpty else { return nil }
        return GlobalShortcut(keyCode: UInt16(chord.keyCode), modifiers: mods, spokenName: spoken(text))
    }

    /// True for a key-down of this key with exactly these modifiers.
    func matches(keyCode: UInt16, flags: NSEvent.ModifierFlags) -> Bool {
        keyCode == self.keyCode && flags.intersection(Self.relevant) == modifiers
    }

    /// Carbon modifier mask for RegisterEventHotKey.
    var carbonModifiers: UInt32 {
        var m: UInt32 = 0
        if modifiers.contains(.command) { m |= UInt32(cmdKey) }
        if modifiers.contains(.option) { m |= UInt32(optionKey) }
        if modifiers.contains(.control) { m |= UInt32(controlKey) }
        if modifiers.contains(.shift) { m |= UInt32(shiftKey) }
        return m
    }

    private static func spoken(_ text: String) -> String {
        let names: [String: String] = [
            "ctrl": "Control", "control": "Control", "opt": "Option", "option": "Option", "alt": "Option",
            "cmd": "Command", "command": "Command", "shift": "Shift", "space": "Space", "spacebar": "Space",
            "return": "Return", "enter": "Return", "escape": "Escape", "esc": "Escape", "tab": "Tab",
        ]
        return text.split(separator: "+").map { part -> String in
            let p = part.trimmingCharacters(in: .whitespaces).lowercased()
            return names[p] ?? p.uppercased()
        }.joined(separator: " ")
    }

    static var typePrompt: GlobalShortcut {
        parse(Config.string("BEACON_TYPE_SHORTCUT", "ctrl+opt+space")) ?? parse("ctrl+opt+space")!
    }

    static var regionSnapshot: GlobalShortcut {
        parse(Config.string("BEACON_SNAPSHOT_SHORTCUT", "ctrl+opt+s")) ?? parse("ctrl+opt+s")!
    }
}

/// Registers global shortcuts and calls their handlers on the main thread.
@MainActor
final class ShortcutCenter {
    private struct Entry {
        let shortcut: GlobalShortcut
        let handler: () -> Void
        var ref: EventHotKeyRef?
    }

    private static let signature: OSType = 0x4243_4E53   // "BCNS"
    private var entries: [UInt32: Entry] = [:]
    private var handlerRef: EventHandlerRef?
    private var fallbackMonitor: Any?

    /// Called before any shortcut handler (AppDelegate cancels a talk-key recording started by the Option press).
    var willFire: (() -> Void)?

    func register(_ shortcut: GlobalShortcut, handler: @escaping () -> Void) {
        installHandlerIfNeeded()
        let id = UInt32(entries.count + 1)
        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(UInt32(shortcut.keyCode), shortcut.carbonModifiers,
                                         EventHotKeyID(signature: Self.signature, id: id),
                                         GetApplicationEventTarget(), 0, &ref)
        entries[id] = Entry(shortcut: shortcut, handler: handler, ref: status == noErr ? ref : nil)
        if status == noErr {
            Log.info("shortcut \(shortcut.spokenName) registered")
        } else {
            Log.warn("shortcut \(shortcut.spokenName) is taken by another app (status \(status)); using a key monitor")
            installFallbackMonitorIfNeeded()
        }
    }

    fileprivate func fire(id: UInt32) {
        guard let entry = entries[id] else { return }
        willFire?()
        entry.handler()
    }

    private func installHandlerIfNeeded() {
        guard handlerRef == nil else { return }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return OSStatus(eventNotHandledErr) }
            var hotKey = EventHotKeyID()
            let status = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                           EventParamType(typeEventHotKeyID), nil,
                                           MemoryLayout<EventHotKeyID>.size, nil, &hotKey)
            guard status == noErr, hotKey.signature == ShortcutCenter.signature else {
                return OSStatus(eventNotHandledErr)
            }
            let center = Unmanaged<ShortcutCenter>.fromOpaque(userData).takeUnretainedValue()
            let id = hotKey.id
            MainActor.assumeIsolated { center.fire(id: id) }
            return noErr
        }, 1, &spec, me, &handlerRef)
    }

    private func installFallbackMonitorIfNeeded() {
        guard fallbackMonitor == nil else { return }
        fallbackMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            let code = event.keyCode
            let flags = event.modifierFlags
            let isRepeat = event.isARepeat
            MainActor.assumeIsolated {
                guard let self, !isRepeat else { return }
                for (id, entry) in self.entries where entry.ref == nil && entry.shortcut.matches(keyCode: code, flags: flags) {
                    self.fire(id: id)
                }
            }
        }
    }
}
