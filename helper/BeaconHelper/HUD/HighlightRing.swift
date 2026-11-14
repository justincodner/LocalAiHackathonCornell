// Owner: Mac owner. Click-through high-contrast ring around the element Beacon acts on (spec 9.6).
import AppKit

/// Converts an Accessibility rect (top-left origin, points, relative to the primary screen) to AppKit
/// screen coordinates (bottom-left origin).
func appKitRect(fromAXRect r: CGRect, primaryScreenHeight h: CGFloat) -> NSRect {
    NSRect(x: r.origin.x, y: h - r.origin.y - r.height, width: r.width, height: r.height)
}

final class RingView: NSView {
    static let ringWidth: CGFloat = 4
    static let outline: CGFloat = 2
    /// Space between the panel edge and the target rect, so the ring sits just outside the element.
    static let padding: CGFloat = ringWidth + outline * 2 + 2

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        let inset = Self.outline + Self.ringWidth / 2
        let rect = bounds.insetBy(dx: inset, dy: inset)
        let path = NSBezierPath(roundedRect: rect, xRadius: 6, yRadius: 6)
        // The black outline keeps the white ring visible on light and dark content.
        NSColor.black.setStroke()
        path.lineWidth = Self.ringWidth + Self.outline * 2
        path.stroke()
        NSColor.white.setStroke()
        path.lineWidth = Self.ringWidth
        path.stroke()
    }
}

@MainActor
final class HighlightRing {
    static let duration: TimeInterval = 1.5
    let panel: OverlayPanel
    private var hideTask: Task<Void, Never>?

    init() {
        panel = OverlayPanel(contentRect: NSRect(x: 0, y: 0, width: 10, height: 10))
        panel.level = .statusBar
        panel.hasShadow = false
        panel.contentView = RingView(frame: panel.contentLayoutRect)
        panel.contentView?.autoresizingMask = [.width, .height]
    }

    /// `rect` is in screen points with a top-left origin (AX coordinates).
    func show(axRect rect: CGRect, reduceMotion: Bool) {
        guard rect.width.isFinite, rect.height.isFinite, rect.width >= 0, rect.height >= 0 else { return }
        let h = NSScreen.screens.first?.frame.height ?? 0
        let frame = appKitRect(fromAXRect: rect, primaryScreenHeight: h)
            .insetBy(dx: -RingView.padding, dy: -RingView.padding)
        hideTask?.cancel()
        panel.setFrame(frame, display: true)
        panel.alphaValue = 1
        panel.contentView?.needsDisplay = true
        panel.orderFrontRegardless()
        hideTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.duration * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.hide(animated: !reduceMotion)
        }
    }

    private func hide(animated: Bool) {
        guard animated else { panel.orderOut(nil); return }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            Task { @MainActor in
                guard let self, self.panel.alphaValue == 0 else { return }
                self.panel.orderOut(nil)
            }
        })
    }
}
