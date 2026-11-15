// Owner: Mac owner. Non-activating panels for the HUD. They must never become key (spec 9.6).
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

enum HUDPlacement: String, CaseIterable {
    case topLeft = "top left"
    case top = "top"
    case topRight = "top right"
    case left = "left"
    case center = "center"
    case right = "right"
    case bottomLeft = "bottom left"
    case bottom = "bottom"
    case bottomRight = "bottom right"

    init?(utterance: String) {
        let words = utterance.lowercased().split(whereSeparator: { !$0.isLetter })
        let command = words.joined(separator: " ")
        let pattern = #"^(?:hey beacon )?(?:move|put|place) (?:the )?(?:beacon(?: (?:hud|overlay|window))?|hud|overlay) (?:to |in |at )?(?:the )?(top left|top right|bottom left|bottom right|top|bottom|left|right|center|centre|middle)(?: corner)?(?: please)?$"#
        guard let range = command.range(of: pattern, options: .regularExpression) else { return nil }
        let direction = command[range].replacingOccurrences(of: pattern, with: "$1", options: .regularExpression)
        self.init(rawValue: ["centre", "middle"].contains(direction) ? "center" : direction)
    }

    func headerCenter(in frame: NSRect, size: NSSize, margin: CGFloat) -> NSPoint {
        let left = frame.minX + size.width / 2 + margin
        let right = frame.maxX - size.width / 2 - margin
        let bottom = frame.minY + size.height / 2 + margin
        let top = frame.maxY - size.height / 2 - margin
        switch self {
        case .topLeft:     return NSPoint(x: left, y: top)
        case .top:         return NSPoint(x: frame.midX, y: top)
        case .topRight:    return NSPoint(x: right, y: top)
        case .left:        return NSPoint(x: left, y: frame.midY)
        case .center:      return NSPoint(x: frame.midX, y: frame.midY)
        case .right:       return NSPoint(x: right, y: frame.midY)
        case .bottomLeft:  return NSPoint(x: left, y: bottom)
        case .bottom:      return NSPoint(x: frame.midX, y: bottom)
        case .bottomRight: return NSPoint(x: right, y: bottom)
        }
    }
}

final class PillPanel: OverlayPanel {
    static let size = NSSize(width: 410, height: 58)
    private static let margin: CGFloat = 16
    private static let positionKey = "HUDHeaderPosition"

    private let preferences: UserDefaults
    private var anchor: NSPoint?
    private var dragOffset: NSPoint?
    private(set) var replyAboveHeader = false

    init(preferences: UserDefaults = .standard) {
        self.preferences = preferences
        if let position = preferences.string(forKey: Self.positionKey) {
            anchor = NSPointFromString(position)
        }
        super.init(contentRect: NSRect(origin: .zero, size: Self.size))
        hasShadow = true
        ignoresMouseEvents = false
        isMovable = true
        isMovableByWindowBackground = false
    }

    private var headerCenter: NSPoint {
        NSPoint(x: frame.midX, y: replyAboveHeader ? frame.minY + Self.size.height / 2
                                                  : frame.maxY - Self.size.height / 2)
    }

    private static func screen(containing point: NSPoint?) -> NSScreen? {
        if let point, let screen = NSScreen.screens.first(where: { $0.frame.contains(point) }) {
            return screen
        }
        return NSScreen.main ?? NSScreen.screens.first
    }

    func dragHeader(with event: NSEvent, finished: () -> Void) {
        switch event.type {
        case .leftMouseDown:
            dragOffset = event.locationInWindow
        case .leftMouseDragged:
            guard let dragOffset else { return }
            let pointer = NSEvent.mouseLocation
            setFrameOrigin(NSPoint(x: pointer.x - dragOffset.x, y: pointer.y - dragOffset.y))
        case .leftMouseUp:
            guard dragOffset != nil else { return }
            dragOffset = nil
            saveAnchor(headerCenter)
            finished()
        default:
            break
        }
    }

    func move(to placement: HUDPlacement) {
        // Use the display under the pointer so the command can also move between displays.
        guard let screen = Self.screen(containing: NSEvent.mouseLocation) else { return }
        let width = min(Self.size.width, screen.visibleFrame.width)
        saveAnchor(placement.headerCenter(in: screen.visibleFrame, size: NSSize(width: width, height: Self.size.height),
                                          margin: Self.margin))
    }

    /// Puts the header at a placement on a given visible frame (used when restoring after Minimize).
    func move(to placement: HUDPlacement, in visibleFrame: NSRect) {
        let width = min(Self.size.width, visibleFrame.width)
        saveAnchor(placement.headerCenter(in: visibleFrame, size: NSSize(width: width, height: Self.size.height),
                                          margin: Self.margin))
    }

    private func saveAnchor(_ point: NSPoint) {
        anchor = point
        preferences.set(NSStringFromPoint(point), forKey: Self.positionKey)
    }

    func layout(reply: String, question: Bool = false) {
        guard dragOffset == nil else { return }
        guard let screen = Self.screen(containing: anchor) else { return }
        let visible = screen.visibleFrame
        let width = min(Self.size.width, visible.width)
        let headerHeight = Self.size.height
        let preferred = anchor ?? HUDPlacement.bottom.headerCenter(
            in: visible, size: NSSize(width: width, height: headerHeight), margin: Self.margin)
        let x = min(max(preferred.x - width / 2, visible.minX), visible.maxX - width)
        let headerY = min(max(preferred.y - headerHeight / 2, visible.minY), visible.maxY - headerHeight)

        let roomAbove = visible.maxY - headerY - headerHeight
        let roomBelow = headerY - visible.minY
        replyAboveHeader = roomAbove > roomBelow
        let replyHeight = Self.replyHeight(for: reply, width: width, available: max(roomAbove, roomBelow), question: question)
        let y = replyAboveHeader ? headerY : headerY - replyHeight
        let newFrame = NSRect(x: x, y: y, width: width, height: headerHeight + replyHeight)
        if frame != newFrame { setFrame(newFrame, display: true) }
    }

    /// Height of the reply area. A question is drawn at 17 pt with a small "QUESTION" label above it.
    static func replyHeight(for reply: String, width: CGFloat, available: CGFloat, question: Bool = false) -> CGFloat {
        guard !reply.isEmpty else { return 0 }
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 4
        let font = question ? NSFont.systemFont(ofSize: 17, weight: .medium) : NSFont.systemFont(ofSize: 16)
        let textSize = (reply as NSString).boundingRect(
            with: NSSize(width: max(1, width - PillView.replyHorizontalPadding * 2 - 4), height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: font, .paragraphStyle: paragraph])
        let label: CGFloat = question ? 18 : 0
        return min(ceil(textSize.height) + 33 + label, 280, max(0, available))
    }
}

/// Where the HUD reappears after Minimize: top-right below the menu bar, unless that would cover more of the
/// frontmost window than top-left. Pure, so it can be unit tested.
enum HUDRestorePlacement {
    /// Room reserved under the header for a short reply when judging what the HUD would cover.
    static let replyAllowance: CGFloat = 120

    /// - Parameters:
    ///   - visibleFrame: the screen's visible frame (AppKit coordinates, below the menu bar).
    ///   - frontWindow: the frontmost app's focused window frame in AppKit coordinates, if known.
    static func choose(visibleFrame: NSRect, frontWindow: NSRect?, size: NSSize = PillPanel.size,
                       margin: CGFloat = 16) -> HUDPlacement {
        guard let frontWindow else { return .topRight }
        let right = covered(.topRight, visibleFrame: visibleFrame, window: frontWindow, size: size, margin: margin)
        let left = covered(.topLeft, visibleFrame: visibleFrame, window: frontWindow, size: size, margin: margin)
        return right <= left ? .topRight : .topLeft
    }

    /// Area of the window the HUD (header plus a short reply) would cover at a placement.
    static func covered(_ placement: HUDPlacement, visibleFrame: NSRect, window: NSRect, size: NSSize,
                        margin: CGFloat) -> CGFloat {
        let width = min(size.width, visibleFrame.width)
        let center = placement.headerCenter(in: visibleFrame, size: NSSize(width: width, height: size.height),
                                            margin: margin)
        let hud = NSRect(x: center.x - width / 2, y: center.y - size.height / 2 - replyAllowance,
                         width: width, height: size.height + replyAllowance)
        let overlap = hud.intersection(window)
        return overlap.isNull ? 0 : overlap.width * overlap.height
    }
}
