// Owner: Mac owner. Click-through, non-activating panels for the HUD. They must never become key (spec 9.6).
import AppKit

/// Borderless non-activating panel that can never become key or main and ignores the mouse.
/// Showing it with `orderFrontRegardless()` never changes the frontmost app.
class OverlayPanel: NSPanel {
    init(contentRect: NSRect) {
        super.init(contentRect: contentRect,
                   styleMask: [.nonactivatingPanel, .borderless],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        becomesKeyOnlyIfNeeded = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        hidesOnDeactivate = false
        ignoresMouseEvents = true
        isOpaque = false
        backgroundColor = .clear
        isReleasedWhenClosed = false
        isMovable = false
        animationBehavior = .none
        isExcludedFromWindowsMenu = true
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// The 520 x 72 pt pill at the bottom centre of the main screen.
final class PillPanel: OverlayPanel {
    static let size = NSSize(width: 520, height: 72)
    static let bottomMargin: CGFloat = 28

    init() {
        super.init(contentRect: NSRect(origin: .zero, size: Self.size))
        hasShadow = true
        reposition()
    }

    /// Bottom centre of the screen that holds the menu bar's key focus (falls back to the primary screen).
    func reposition() {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let vf = screen.visibleFrame
        let origin = NSPoint(x: (vf.midX - Self.size.width / 2).rounded(),
                             y: (vf.minY + Self.bottomMargin).rounded())
        setFrame(NSRect(origin: origin, size: Self.size), display: true)
    }
}
