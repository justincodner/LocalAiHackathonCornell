// Owner: Mac owner. Full-screen crosshair overlay for dragging a rectangle (Control+Option+S).
// One transparent panel per display. The panels are non-activating, so the app the user is looking at stays
// frontmost; the panel under the pointer becomes key only so Escape reaches it.
import AppKit

@MainActor
final class RegionSelector {
    /// Called once with the selected rectangle (top-left-origin global points), or nil if cancelled.
    private var completion: ((CGRect?) -> Void)?
    private var panels: [SelectionPanel] = []
    private var keyMonitors: [Any] = []
    private var start: NSPoint?
    private var current: NSPoint?

    var isActive: Bool { completion != nil }

    func begin(completion: @escaping (CGRect?) -> Void) {
        guard self.completion == nil else { return }
        self.completion = completion
        start = nil
        current = nil
        for screen in NSScreen.screens {
            let panel = SelectionPanel(screen: screen)
            let view = SelectionView(frame: NSRect(origin: .zero, size: screen.frame.size))
            view.selector = self
            panel.contentView = view
            panel.orderFrontRegardless()
            panels.append(panel)
        }
        let pointer = NSEvent.mouseLocation
        (panels.first { $0.frame.contains(pointer) } ?? panels.first)?.makeKey()
        NSCursor.crosshair.push()
        // Escape cancels even if a key event goes elsewhere.
        if let local = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { [weak self] event in
            guard event.keyCode == 53 else { return event }
            MainActor.assumeIsolated { self?.finish(nil) }
            return nil
        }) { keyMonitors.append(local) }
        if let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown, handler: { [weak self] event in
            guard event.keyCode == 53 else { return }
            MainActor.assumeIsolated { self?.finish(nil) }
        }) { keyMonitors.append(global) }
    }

    func cancel() { finish(nil) }

    fileprivate func mouseDown(at point: NSPoint) {
        start = point
        current = point
        redraw()
    }

    fileprivate func mouseDragged(to point: NSPoint) {
        guard start != nil else { return }
        current = point
        redraw()
    }

    fileprivate func mouseUp(at point: NSPoint) {
        guard let start else { return }
        let appKit = RegionGeometry.rect(from: start, to: point)
        let height = NSScreen.screens.first?.frame.height ?? 0
        finish(RegionGeometry.topLeftRect(fromAppKit: appKit, primaryScreenHeight: height))
    }

    /// Selection in AppKit global coordinates while dragging.
    fileprivate var selection: NSRect? {
        guard let start, let current else { return nil }
        return RegionGeometry.rect(from: start, to: current)
    }

    private func redraw() {
        for panel in panels { panel.contentView?.needsDisplay = true }
    }

    private func finish(_ rect: CGRect?) {
        guard let completion else { return }
        self.completion = nil
        for m in keyMonitors { NSEvent.removeMonitor(m) }
        keyMonitors.removeAll()
        NSCursor.pop()
        for panel in panels { panel.orderOut(nil) }
        panels.removeAll()
        completion(rect)
    }
}

/// Borderless overlay that may become key (for Escape) without activating Beacon.
final class SelectionPanel: NSPanel {
    init(screen: NSScreen) {
        super.init(contentRect: screen.frame, styleMask: [.nonactivatingPanel, .borderless],
                   backing: .buffered, defer: false)
        setFrame(screen.frame, display: false)
        level = .screenSaver
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        acceptsMouseMovedEvents = true
        animationBehavior = .none
        isExcludedFromWindowsMenu = true
        setAccessibilityLabel("Screen selection")
        setAccessibilityHelp("Drag a rectangle around what you want to ask about. Press Escape to cancel.")
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

private final class SelectionView: NSView {
    weak var selector: RegionSelector?

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }

    override func resetCursorRects() { addCursorRect(bounds, cursor: .crosshair) }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.cursorUpdate, .activeAlways, .inVisibleRect],
                                       owner: self))
    }

    override func cursorUpdate(with event: NSEvent) { NSCursor.crosshair.set() }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { selector?.cancel() } else { super.keyDown(with: event) }
    }

    override func mouseDown(with event: NSEvent) { selector?.mouseDown(at: NSEvent.mouseLocation) }
    override func mouseDragged(with event: NSEvent) { selector?.mouseDragged(to: NSEvent.mouseLocation) }
    override func mouseUp(with event: NSEvent) { selector?.mouseUp(at: NSEvent.mouseLocation) }
    override func rightMouseDown(with event: NSEvent) { selector?.cancel() }

    override func draw(_ dirtyRect: NSRect) {
        guard let window else { return }
        NSColor.black.withAlphaComponent(0.25).setFill()
        bounds.fill()
        // Hint text, so sighted helpers know what the dimmed screen is for.
        let hint = "Drag around what you want to ask about. Esc cancels." as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 15, weight: .semibold), .foregroundColor: NSColor.white,
        ]
        let size = hint.size(withAttributes: attrs)
        let hintRect = NSRect(x: bounds.midX - size.width / 2 - 12, y: bounds.maxY - 90, width: size.width + 24,
                              height: size.height + 12)
        NSColor.black.withAlphaComponent(0.75).setFill()
        NSBezierPath(roundedRect: hintRect, xRadius: 8, yRadius: 8).fill()
        hint.draw(at: NSPoint(x: hintRect.minX + 12, y: hintRect.minY + 6), withAttributes: attrs)

        guard let global = selector?.selection else { return }
        let local = global.offsetBy(dx: -window.frame.minX, dy: -window.frame.minY)
        guard local.intersects(bounds) else { return }
        NSGraphicsContext.current?.compositingOperation = .clear
        local.fill()
        NSGraphicsContext.current?.compositingOperation = .sourceOver
        let path = NSBezierPath(rect: local.insetBy(dx: -1, dy: -1))
        path.lineWidth = 3
        NSColor.black.setStroke()
        path.stroke()
        let inner = NSBezierPath(rect: local.insetBy(dx: -1, dy: -1))
        inner.lineWidth = 1.5
        NSColor.white.setStroke()   // monochrome, like the HUD
        inner.stroke()
    }
}
