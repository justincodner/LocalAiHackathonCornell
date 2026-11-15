// Owner: Mac owner. Global push-to-talk key monitor (spec 9.1). Uses flagsChanged, which needs Accessibility.
import AppKit
import Carbon.HIToolbox

@MainActor
final class HotkeyMonitor {
    private var globalMonitor: Any?
    private var localMonitor: Any?
    private var isDown = false
    private let keyCode: UInt16
    private let flag: NSEvent.ModifierFlags
    var onPress: (() -> Void)?
    var onRelease: (() -> Void)?

    init(keyName: String = Config.pttKey) {
        switch keyName.lowercased() {
        case "right_command", "right_cmd": (keyCode, flag) = (UInt16(kVK_RightCommand), .command)
        case "right_control", "right_ctrl": (keyCode, flag) = (UInt16(kVK_RightControl), .control)
        case "right_shift": (keyCode, flag) = (UInt16(kVK_RightShift), .shift)
        case "fn", "function": (keyCode, flag) = (UInt16(kVK_Function), .function)
        default: (keyCode, flag) = (UInt16(kVK_RightOption), .option)
        }
    }

    func start() {
        let handler: (NSEvent) -> Void = { [weak self] event in
            MainActor.assumeIsolated { self?.handle(event) }
        }
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged, handler: handler)
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { event in
            handler(event)
            return event
        }
        Log.info("push-to-talk key: \(Config.pttKey) (keyCode \(keyCode))")
    }

    private func handle(_ event: NSEvent) {
        guard event.keyCode == keyCode else { return }
        let down = event.modifierFlags.contains(flag)
        if down, !isDown {
            // Control+Option+Space and Control+Option+S: an Option pressed after Control is part of a shortcut.
            guard Self.startsTalk(flags: event.modifierFlags, talkFlag: flag) else { return }
            isDown = true
            onPress?()
        } else if !down, isDown {
            isDown = false
            onRelease?()
        }
    }

    /// The talk key starts listening only when no other modifier is already held, so pressing Option as part of
    /// a Control+Option shortcut does not open the microphone. Pure, so it is unit tested.
    nonisolated static func startsTalk(flags: NSEvent.ModifierFlags, talkFlag: NSEvent.ModifierFlags) -> Bool {
        let others = NSEvent.ModifierFlags([.command, .option, .control, .shift]).subtracting(talkFlag)
        return flags.intersection(others).isEmpty
    }
}
