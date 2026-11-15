// Owner: Mac owner. "Type a request" field (Control+Option+Space or the HUD keyboard button).
// Unlike the HUD pill, this panel can become key so the text field receives typing. It is non-activating, so the
// app the user was in normally stays frontmost; if the panel cannot take keyboard focus that way, Beacon
// activates and hands focus back to the previous app on Return or Escape, before the request runs.
import AppKit

@MainActor
final class TypedPromptPanel: NSPanel, NSTextFieldDelegate {
    static let height: CGFloat = 54

    private let field = PromptField()
    private let label = NSTextField(labelWithString: "")
    private var previousApp: NSRunningApplication?
    private var activatedBeacon = false
    /// Called with the trimmed text on Return (never empty).
    var onSubmit: ((String) -> Void)?
    var onCancel: (() -> Void)?

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: PillPanel.size.width, height: Self.height),
                   styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        hidesOnDeactivate = false
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        isReleasedWhenClosed = false
        animationBehavior = .none
        isExcludedFromWindowsMenu = true
        appearance = NSAppearance(named: .darkAqua)   // white caret and text on the dark HUD surface
        setAccessibilityLabel("Type a request for Beacon")

        let background = NSView()
        background.wantsLayer = true
        // Same near-black surface as the HUD header, so the text keeps its contrast over any content.
        background.layer?.backgroundColor = NSColor(white: 0.09, alpha: 0.96).cgColor
        background.layer?.cornerRadius = 12
        background.layer?.borderWidth = 2
        background.layer?.borderColor = NSColor.white.cgColor
        background.layer?.masksToBounds = true
        background.appearance = NSAppearance(named: .darkAqua)

        label.font = .systemFont(ofSize: 11, weight: .semibold)
        label.textColor = NSColor(white: 0.85, alpha: 1)
        label.setAccessibilityElement(false)

        field.font = .systemFont(ofSize: 16)
        field.focusRingType = .none
        field.isBezeled = false
        field.drawsBackground = false
        field.textColor = .white
        field.delegate = self
        field.cell?.isScrollable = true
        field.cell?.wraps = false
        field.lineBreakMode = .byTruncatingHead
        field.cancel = { [weak self] in self?.cancel() }

        for v in [label, field] {
            v.translatesAutoresizingMaskIntoConstraints = false
            background.addSubview(v)
        }
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: background.leadingAnchor, constant: 16),
            label.trailingAnchor.constraint(equalTo: background.trailingAnchor, constant: -16),
            label.topAnchor.constraint(equalTo: background.topAnchor, constant: 7),
            field.leadingAnchor.constraint(equalTo: background.leadingAnchor, constant: 16),
            field.trailingAnchor.constraint(equalTo: background.trailingAnchor, constant: -16),
            field.topAnchor.constraint(equalTo: label.bottomAnchor, constant: 2),
        ])
        contentView = background
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    /// Shows the field next to the HUD header. `heading` says what the typed text is for.
    func present(near pillFrame: NSRect, heading: String, placeholder: String) {
        configure(heading: heading, placeholder: placeholder)
        setFrame(Self.frame(near: pillFrame, screens: NSScreen.screens.map(\.visibleFrame)), display: true)

        let me = ProcessInfo.processInfo.processIdentifier
        if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != me {
            previousApp = front
        }
        makeKeyAndOrderFront(nil)
        makeFirstResponder(field)
        // A non-activating panel normally takes typing without activating Beacon. If it did not, activate.
        if !isKeyWindow {
            activatedBeacon = true
            NSApp.activate()
            makeKeyAndOrderFront(nil)
            makeFirstResponder(field)
        }
    }

    /// Sets the heading and placeholder (and their accessibility text) without showing the panel.
    func configure(heading: String, placeholder: String) {
        label.stringValue = heading.uppercased()
        field.placeholderAttributedString = NSAttributedString(string: placeholder, attributes: [
            .foregroundColor: NSColor(white: 0.70, alpha: 1), .font: NSFont.systemFont(ofSize: 16),
        ])
        field.setAccessibilityLabel(heading)
        field.setAccessibilityHelp("\(placeholder) Press Return to send, or Escape to cancel.")
        if !isVisible { field.stringValue = "" }
    }

    /// Accessibility label of the text field (for tests).
    var fieldAccessibilityLabel: String? { field.accessibilityLabel() }

    /// Below the HUD pill if there is room on its screen, else above it.
    static func frame(near pill: NSRect, screens: [NSRect], gap: CGFloat = 8) -> NSRect {
        let visible = screens.first { $0.intersects(pill) } ?? screens.first ?? pill
        let width = pill.width
        var y = pill.minY - gap - height
        if y < visible.minY { y = pill.maxY + gap }
        y = min(max(y, visible.minY), visible.maxY - height)
        let x = min(max(pill.minX, visible.minX), visible.maxX - width)
        return NSRect(x: x, y: y, width: width, height: height)
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        switch selector {
        case #selector(NSResponder.insertNewline(_:)):
            let text = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { NSSound.beep(); return true }
            close(returningFocus: true)
            onSubmit?(text)
            return true
        case #selector(NSResponder.cancelOperation(_:)):
            cancel()
            return true
        default:
            return false
        }
    }

    func cancel() {
        guard isVisible else { return }
        close(returningFocus: true)
        onCancel?()
    }

    private func close(returningFocus: Bool) {
        orderOut(nil)
        field.stringValue = ""
        if returningFocus, activatedBeacon || NSApp.isActive {
            previousApp?.activate()
        }
        activatedBeacon = false
    }
}

/// Text field whose Escape also works when the field editor is not active yet.
private final class PromptField: NSTextField {
    var cancel: (() -> Void)?
    override func cancelOperation(_ sender: Any?) { cancel?() }
}
