// Owner: Mac owner (H2, H3). Snapshots, find, read and actions over the frontmost app's accessibility tree.
// Runs on whatever thread calls it; never needs the main actor.
import AppKit
import ApplicationServices
import Foundation

final class AccessibilityService: AccessibilityProviding, @unchecked Sendable {
    struct Stored {
        let element: AXUIElement
        /// Full merged text, when the element stands for several static texts.
        let fullText: String?
    }

    let table = ElementTable<Stored>()
    private let lock = NSLock()
    private var lastExternalPID: pid_t?

    /// Snapshot wall-clock budget (spec 5.4 step 8); a little is left for building and serialising.
    static let walkBudget: TimeInterval = 0.56
    static let rawDepthLimit = 60

    init() {
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), AX.messagingTimeout)
    }

    // MARK: Target app

    struct Target {
        let pid: pid_t
        let name: String
        let app: AXUIElement
    }

    func frontmostTarget() throws -> Target {
        guard AXIsProcessTrusted() else {
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        }
        let own = getpid()
        var pid: pid_t?
        var name: String?
        if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != own {
            pid = front.processIdentifier
            name = front.localizedName
        } else if let last = lock.withLock({ lastExternalPID }),
                  let app = NSRunningApplication(processIdentifier: last), !app.isTerminated {
            pid = last
            name = app.localizedName
        } else if let w = Self.frontmostWindowOwner(excluding: own) {
            (pid, name) = (w.0, w.1)
        }
        guard let pid else { throw HelperError(.appNotFound, "no frontmost app to look at") }
        guard pid != own else {
            throw HelperError(.permissionDenied, "Beacon does not snapshot its own windows")
        }
        lock.withLock { lastExternalPID = pid }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, AX.messagingTimeout)
        let resolved = name ?? NSRunningApplication(processIdentifier: pid)?.localizedName ?? "pid \(pid)"
        return Target(pid: pid, name: resolved, app: app)
    }

    /// The owner of the front-most normal on-screen window that is not ours (most recently active other app).
    static func frontmostWindowOwner(excluding own: pid_t) -> (pid_t, String)? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] else { return nil }
        for w in list {
            guard (w[kCGWindowLayer as String] as? Int) == 0,
                  let pid = w[kCGWindowOwnerPID as String] as? pid_t, pid != own,
                  let app = NSRunningApplication(processIdentifier: pid), app.activationPolicy == .regular else { continue }
            return (pid, app.localizedName ?? (w[kCGWindowOwnerName as String] as? String) ?? "pid \(pid)")
        }
        return nil
    }

    /// An open menu (context menu or menu-bar menu), if any.
    func openMenu(_ app: AXUIElement) -> AXUIElement? {
        for c in AX.elements(AX.attribute(app, "AXChildren")) where AX.role(c) == "AXMenu" { return c }
        guard let bar = AX.element(AX.attribute(app, "AXMenuBar")) else { return nil }
        for item in AX.elements(AX.attribute(bar, "AXChildren")) {
            guard AX.bool(AX.attribute(item, "AXSelected")) == true else { continue }
            if let menu = AX.elements(AX.attribute(item, "AXChildren")).first(where: { AX.role($0) == "AXMenu" }) {
                return menu
            }
        }
        return nil
    }

    func focusedWindow(_ app: AXUIElement, appName: String) throws -> AXUIElement {
        if let w = AX.element(AX.attribute(app, "AXFocusedWindow")) { return w }
        if let w = AX.element(AX.attribute(app, "AXMainWindow")) { return w }
        if let w = AX.elements(AX.attribute(app, "AXWindows")).first { return w }
        throw HelperError(.elementNotFound, "\(appName) has no open window")
    }

    // MARK: Snapshot

    func snapshot(scope: String, maxElements: Int) async throws -> JSONObject {
        let started = Date()
        let target = try frontmostTarget()
        let focusedEl = AX.element(AX.attribute(target.app, "AXFocusedUIElement"))

        var windowTitle = ""
        var root: AXUIElement
        var includeRoot = false
        var clip = AX.displaysFrame()

        let menu = openMenu(target.app)
        if scope == "menu" {
            guard let menu else { throw HelperError(.elementNotFound, "no menu is open in \(target.name)") }
            root = menu
            windowTitle = "menu"
        } else if let menu, scope == "window" {
            root = menu
            windowTitle = "menu"
        } else {
            let window = try focusedWindow(target.app, appName: target.name)
            windowTitle = Labels.withoutTabNoise(AX.string(AX.attribute(window, "AXTitle")) ?? "")
            root = window
            if let sheet = AX.elements(AX.attribute(window, "AXChildren")).first(where: { AX.role($0) == "AXSheet" }) {
                root = sheet
            }
            if let f = AX.frame(window), f.width > 0, f.height > 0 {
                clip = clip.map { $0.intersection(f) } ?? f
            }
            if scope == "focused", let focusedEl {
                root = focusedEl
                includeRoot = true
            }
        }

        let walker = AXWalker(deadline: started.addingTimeInterval(Self.walkBudget),
                              maxNodes: max(3000, maxElements * 12), maxDepth: Self.rawDepthLimit,
                              prune: true, focused: focusedEl)
        guard let tree = walker.walk(root, clip: clip) else {
            if walker.truncated { throw HelperError(.timeout, "\(target.name) did not answer in time") }
            throw HelperError(.actionFailed, "could not read \(target.name)'s window (it may be busy)")
        }
        let built = SnapshotBuilder.build(root: tree, includeRoot: includeRoot, maxElements: maxElements,
                                          walkTruncated: walker.truncated)
        let sid = table.nextID()
        var entries: [Int: Stored] = [:]
        for e in built.elements {
            guard let ref = e.ref, ref < walker.refs.count else { continue }
            entries[e.id] = Stored(element: walker.refs[ref], fullText: e.fullText)
        }
        table.register(sid, entries)

        let text = TextBlock.render(app: target.name, window: windowTitle, focusedID: built.focusedID, lines: built.lines)
        let ms = Int(Date().timeIntervalSince(started) * 1000)
        Log.info("snapshot \(sid) \(target.name): \(built.elements.count)/\(built.totalElements) elements, " +
                 "\(walker.nodeCount) nodes walked, truncated=\(built.truncated), \(ms) ms")
        return [
            "snapshot_id": sid,
            "app": target.name,
            "window": windowTitle,
            "focused_id": built.focusedID.map { $0 as Any } ?? NSNull(),
            "truncated": built.truncated,
            "total_elements": built.totalElements,
            "build_ms": ms,
            "elements": built.elements.map(\.json),
            "text": text,
        ]
    }

    // MARK: Screen text (speech hints)

    /// Labels of the frontmost window's visible elements, for the Whisper prompt. Cheaper than `snapshot`
    /// (shorter budget, no element registration, so the Brain's snapshot ids are never evicted). Best effort:
    /// nil when accessibility is unavailable or the app does not answer.
    func screenText(maxElements: Int = 200, budget: TimeInterval = 0.3) -> ScreenText? {
        guard let target = try? frontmostTarget(),
              let window = try? focusedWindow(target.app, appName: target.name) else { return nil }
        let title = AX.string(AX.attribute(window, "AXTitle")) ?? ""
        var root = window
        if let sheet = AX.elements(AX.attribute(window, "AXChildren")).first(where: { AX.role($0) == "AXSheet" }) {
            root = sheet
        }
        var clip = AX.displaysFrame()
        if let f = AX.frame(window), f.width > 0, f.height > 0 {
            clip = clip.map { $0.intersection(f) } ?? f
        }
        let walker = AXWalker(deadline: Date().addingTimeInterval(budget), maxNodes: maxElements * 12,
                              maxDepth: Self.rawDepthLimit, prune: true, focused: nil)
        guard let tree = walker.walk(root, clip: clip) else {
            return ScreenText(app: target.name, window: title, labels: [])
        }
        let built = SnapshotBuilder.build(root: tree, includeRoot: false, maxElements: maxElements,
                                          walkTruncated: walker.truncated)
        let labels = built.elements.compactMap { e -> String? in
            e.labelSource == "inferred" || e.label.isEmpty ? nil : e.label
        }
        return ScreenText(app: target.name, window: title, labels: labels)
    }

    // MARK: Find

    func find(query: String, limit: Int) async throws -> [JSONObject] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { throw HelperError(.badRequest, "query must not be empty") }
        let limit = min(max(limit, 1), 50)
        let target = try frontmostTarget()
        let focusedEl = AX.element(AX.attribute(target.app, "AXFocusedUIElement"))
        let root = try openMenu(target.app) ?? focusedWindow(target.app, appName: target.name)
        let deadline = Date().addingTimeInterval(1.5)

        var matches: [(AXNode, Int, AXUIElement)] = []
        var visited = 0
        func visit(_ el: AXUIElement, depth: Int) {
            guard matches.count < limit, visited < 6000, Date() < deadline, depth <= Self.rawDepthLimit else { return }
            visited += 1
            guard let (node, children) = AX.readNode(el, focused: focusedEl) else { return }
            if depth > 0, Self.matches(node, q) { matches.append((node, depth, el)) }
            if node.axRole == "AXMenuItem" { return }
            for c in children { visit(c, depth: depth + 1) }
        }
        visit(root, depth: 0)

        let sid = table.nextID()
        var entries: [Int: Stored] = [:]
        var out: [JSONObject] = []
        for (i, (node, depth, el)) in matches.enumerated() {
            let id = i + 1
            let role = RoleMap.shortRole(axRole: node.axRole, subrole: node.subrole)
            let o = SnapshotBuilder.makeOut(node, role: role)
            let el2 = SnapshotElement(id: id, depth: min(depth - 1, SnapshotBuilder.maxDepth), role: role,
                                      label: o.label, labelSource: o.label.isEmpty ? "none" : o.labelSource,
                                      value: o.value, states: SnapshotBuilder.stateOrder.filter { o.states.contains($0) },
                                      frame: node.frame, ref: nil, fullText: nil, rowCount: nil)
            entries[id] = Stored(element: el, fullText: nil)
            var j = el2.json
            j["snapshot_id"] = sid
            if role == "link", o.fullLabel != o.label { j["full_label"] = o.fullLabel }
            out.append(j)
        }
        table.register(sid, entries)
        Log.info("find '\(query)' in \(target.name): \(out.count) matches, \(visited) nodes, snapshot \(sid)")
        return out
    }

    static func matches(_ n: AXNode, _ q: String) -> Bool {
        let fields: [String?] = [n.title, n.desc, n.isSecure ? nil : n.value?.text, n.identifier, n.help,
                                 n.titleElementText, n.placeholder]
        return fields.contains { $0?.lowercased().contains(q) == true }
    }

    // MARK: Read

    func read(snapshotID: String, elementID: Int) async throws -> String {
        let stored = try table.resolve(snapshotID: snapshotID, elementID: elementID)
        if let t = stored.fullText { return t }
        let deadline = Date().addingTimeInterval(2.0)
        var pieces: [(String, CGRect?)] = []
        var visited = 0
        var alive = false
        func visit(_ el: AXUIElement) {
            guard visited < 5000, Date() < deadline else { return }
            visited += 1
            guard let (n, children) = AX.readNode(el, focused: nil) else { return }
            alive = true
            switch n.axRole {
            case "AXStaticText":
                if let t = n.value?.text ?? n.title, !t.isEmpty { pieces.append((t, n.frame)) }
                return
            case "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField":
                let t = n.isSecure ? "(hidden)" : (n.value?.text ?? "")
                if !t.isEmpty { pieces.append((t, n.frame)) }
                return
            default:
                if children.isEmpty {
                    if let t = Labels.clean(n.title) ?? Labels.clean(n.desc) ?? Labels.clean(n.value?.text) {
                        pieces.append((t, n.frame))
                    }
                    return
                }
                if n.axRole != "AXRow", let t = Labels.clean(n.title), ["AXButton", "AXLink", "AXHeading"].contains(n.axRole) == false {
                    // Containers with a title (groups, windows): the title heads their content.
                    pieces.append((t, n.frame))
                }
            }
            for c in children { visit(c) }
        }
        visit(stored.element)
        guard alive else { throw HelperError(.elementNotFound, "element \(elementID) no longer exists; take a new snapshot") }
        return Self.joinPieces(pieces)
    }

    /// Joins text pieces with a space when they sit on the same line, a newline otherwise.
    static func joinPieces(_ pieces: [(String, CGRect?)]) -> String {
        var out = ""
        var prev: CGRect?
        for (t, f) in pieces {
            if !out.isEmpty {
                if let p = prev, let f, f.minY < p.maxY - 2, f.minX >= p.minX { out += " " } else { out += "\n" }
            }
            out += t
            prev = f ?? prev
        }
        return out
    }

    // MARK: Act

    func act(snapshotID: String, elementID: Int, action: String, value: Any?) async throws -> String {
        let el = try table.resolve(snapshotID: snapshotID, elementID: elementID).element
        switch action {
        case "press":
            // Web pages often act only on real pointer events (pointerdown/up, mousedown), and
            // Chrome's AXPress sends neither: it reports success and the page does nothing.
            // So in web content, click the element for real when it is visibly on top.
            if Self.isInWebArea(el) {
                return try await pressWebElement(el)
            }
            return try withClickFallback(el, AXUIElementPerformAction(el, kAXPressAction as CFString), what: "press")
        case "focus":
            return try withClickFallback(el, AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue),
                                         what: "focus")
        case "show_menu":
            return try withClickFallback(el, AXUIElementPerformAction(el, kAXShowMenuAction as CFString),
                                         what: "show the menu of", right: true)
        case "increment", "decrement":
            let name = action == "increment" ? kAXIncrementAction : kAXDecrementAction
            let err = AXUIElementPerformAction(el, name as CFString)
            try check(err, what: action)
            return "ax"
        case "set_value":
            guard let cf = Self.cfValue(value, current: AX.attribute(el, "AXValue")) else {
                throw HelperError(.badRequest, "value must be a string, number or boolean")
            }
            let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, cf)
            try check(err, what: "set the value of")
            return "ax"
        default:
            throw HelperError(.badRequest, "unknown action \(action)")
        }
    }

    /// Converts a JSON value for AXValue, matching the element's current value type when it is numeric.
    static func cfValue(_ value: Any?, current: AnyObject?) -> CFTypeRef? {
        let currentIsNumber = current.map { CFGetTypeID($0) == CFNumberGetTypeID() } ?? false
        switch value {
        case let s as String:
            if currentIsNumber, let d = Double(s) { return d as NSNumber }
            return s as CFString
        case let n as NSNumber:
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return n.boolValue ? kCFBooleanTrue : kCFBooleanFalse }
            return currentIsNumber ? n : (n.stringValue as CFString)
        case let b as Bool:
            return b ? kCFBooleanTrue : kCFBooleanFalse
        default:
            return nil
        }
    }

    private func check(_ err: AXError, what: String) throws {
        switch err {
        case .success, .cannotComplete:
            return  // cannotComplete: the app is busy handling it (e.g. a modal opened); do not retry.
        case .invalidUIElement:
            throw HelperError(.elementNotFound, "the element no longer exists; take a new snapshot")
        case .apiDisabled:
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        case .actionUnsupported, .attributeUnsupported, .notImplemented:
            throw HelperError(.actionUnsupported, "this element cannot \(what) (AX error \(err.rawValue))")
        default:
            throw HelperError(.actionFailed, "could not \(what) the element (AX error \(err.rawValue))")
        }
    }

    /// AX result, or a synthetic click at the element's centre when the AX action fails (spec 4.4).
    private func withClickFallback(_ el: AXUIElement, _ err: AXError, what: String, right: Bool = false) throws -> String {
        switch err {
        case .success, .cannotComplete:
            return "ax"
        case .invalidUIElement:
            throw HelperError(.elementNotFound, "the element no longer exists; take a new snapshot")
        case .apiDisabled:
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        default:
            guard let f = AX.frame(el), f.width > 0, f.height > 0 else {
                throw HelperError(.actionFailed, "could not \(what) the element and it has no on-screen frame to click")
            }
            if let screen = AX.displaysFrame(), !screen.contains(CGPoint(x: f.midX, y: f.midY)) {
                throw HelperError(.actionFailed, "the element is off screen; scroll it into view first")
            }
            try SyntheticEvents.click(at: CGPoint(x: f.midX, y: f.midY), right: right)
            return "click"
        }
    }

    // MARK: Web clicks

    private func pressWebElement(_ el: AXUIElement) async throws -> String {
        var point = webClickPoint(el)
        if point == nil {
            let scrolled = AXUIElementPerformAction(el, "AXScrollToVisible" as CFString)
            if scrolled == .success || scrolled == .cannotComplete {
                let deadline = ContinuousClock.now + .milliseconds(800)
                repeat {
                    try await Task.sleep(for: .milliseconds(100))
                    point = webClickPoint(el)
                } while point == nil && ContinuousClock.now < deadline
            }
        }
        try Task.checkCancellation()
        if let point {
            try SyntheticEvents.click(at: point, restoreCursor: true)
            return "click"
        }
        // AXPress can report success on a Chrome link without activating it. A coordinate fallback
        // here could hit an overlay or whatever is at the edge of the page.
        if AX.role(el) == "AXLink" {
            throw HelperError(.actionFailed, "the web link could not be reached on screen")
        }
        try check(AXUIElementPerformAction(el, kAXPressAction as CFString), what: "press")
        return "ax"
    }

    /// Find an on-screen point that hit-tests to the web element or one of its children.
    private func webClickPoint(_ el: AXUIElement) -> CGPoint? {
        guard let f = AX.frame(el) else { return nil }
        // Chrome pins a scrolled-out element to the viewport edge as a 0-1 px sliver: not clickable.
        guard f.width >= 3, f.height >= 3 else { return nil }
        let screen = AX.displaysFrame()
        let system = AXUIElementCreateSystemWide()
        var elPID: pid_t = 0
        AXUIElementGetPid(el, &elPID)
        // The centre first, then other spots inside the element: part of it may sit under the HUD.
        for point in Self.clickCandidates(f) where screen.map({ $0.contains(point) }) ?? true {
            var hit: AXUIElement?
            guard AXUIElementCopyElementAtPosition(system, Float(point.x), Float(point.y), &hit)
                    == .success, let hit else { continue }
            var hitPID: pid_t = 0
            AXUIElementGetPid(hit, &hitPID)
            // Another window (the HUD, a popup) on top: a click there would not reach the element.
            if elPID == hitPID, Self.isSelfOrAncestor(el, of: hit) { return point }
        }
        return nil
    }

    /// Centre, then left/right of centre, then the upper and lower parts of the left edge.
    static func clickCandidates(_ f: CGRect) -> [CGPoint] {
        let inset = min(12, f.width / 4)
        return [CGPoint(x: f.midX, y: f.midY),
                CGPoint(x: f.minX + inset, y: f.midY),
                CGPoint(x: f.maxX - inset, y: f.midY),
                CGPoint(x: f.minX + inset, y: f.minY + min(10, f.height / 4)),
                CGPoint(x: f.minX + inset, y: f.maxY - min(10, f.height / 4))]
    }

    static func isInWebArea(_ el: AXUIElement) -> Bool {
        var current: AXUIElement? = el
        for _ in 0..<80 {
            guard let c = current else { return false }
            if AX.role(c) == "AXWebArea" { return true }
            if AX.role(c) == "AXWindow" { return false }
            current = AX.element(AX.attribute(c, "AXParent"))
        }
        return false
    }

    /// True when `el` is `hit` or one of its ancestors (the click lands on the button's own text).
    static func isSelfOrAncestor(_ el: AXUIElement, of hit: AXUIElement) -> Bool {
        var current: AXUIElement? = hit
        for _ in 0..<12 {
            guard let c = current else { return false }
            if CFEqual(c, el) { return true }
            if AX.role(c) == "AXWebArea" { return false }
            current = AX.element(AX.attribute(c, "AXParent"))
        }
        return false
    }

    // MARK: Frame

    func frame(snapshotID: String, elementID: Int) async throws -> CGRect {
        let el = try table.resolve(snapshotID: snapshotID, elementID: elementID).element
        guard let f = AX.frame(el) else {
            throw HelperError(.elementNotFound, "element \(elementID) has no frame; take a new snapshot")
        }
        return f
    }
}
