// Owner: Mac owner (H2). Low-level AX reads and the bounded tree walker. Everything here is synchronous and
// thread-agnostic: it never needs the main thread (the `dump` command calls it while main is blocked).
import AppKit
import ApplicationServices
import Foundation

enum AX {
    static let messagingTimeout: Float = 0.4

    /// Attributes fetched for every element in one AXUIElementCopyMultipleAttributeValues call (spec 4.4).
    static let nodeAttributes: [String] = [
        "AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXHelp", "AXIdentifier", "AXEnabled",
        "AXFocused", "AXSelected", "AXPosition", "AXSize", "AXChildren", "AXTitleUIElement", "AXExpanded",
        "AXPlaceholderValue",
    ]

    enum ReadResult {
        case ok([String: AnyObject])
        case failed(AXError)
    }

    static func attributes(_ el: AXUIElement, _ names: [String]) -> ReadResult {
        var errors: [String: AXError] = [:]
        return attributes(el, names, errors: &errors)
    }

    /// Same, also reporting the per-attribute errors (an attribute that failed is absent from the result).
    static func attributes(_ el: AXUIElement, _ names: [String], errors: inout [String: AXError]) -> ReadResult {
        var values: CFArray?
        let err = AXUIElementCopyMultipleAttributeValues(el, names as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &values)
        guard err == .success, let values else { return .failed(err == .success ? .failure : err) }
        var out: [String: AnyObject] = [:]
        let count = CFArrayGetCount(values)
        for i in 0..<min(count, names.count) {
            guard let raw = CFArrayGetValueAtIndex(values, i) else { continue }
            let v = Unmanaged<AnyObject>.fromOpaque(raw).takeUnretainedValue()
            if CFGetTypeID(v) == AXValueGetTypeID(), AXValueGetType(v as! AXValue) == .axError {
                var e = AXError.failure
                if AXValueGetValue(v as! AXValue, .axError, &e) { errors[names[i]] = e } else { errors[names[i]] = .failure }
                continue
            }
            out[names[i]] = v
        }
        return .ok(out)
    }

    /// Errors that mean "the app did not answer in time / is busy", worth one retry.
    static func isTransient(_ e: AXError?) -> Bool { e == .cannotComplete }

    /// AXChildren read on its own (used to retry a failed or not-yet-populated child list).
    static func children(_ el: AXUIElement) -> (refs: [AXUIElement], error: AXError?) {
        var v: CFTypeRef?
        let err = AXUIElementCopyAttributeValue(el, "AXChildren" as CFString, &v)
        guard err == .success else { return ([], err) }
        return (elements(v as AnyObject?), nil)
    }

    static func attribute(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success, let v else { return nil }
        return v as AnyObject
    }

    static func string(_ v: AnyObject?) -> String? {
        guard let v else { return nil }
        if let s = v as? String { return s }
        if let a = v as? NSAttributedString { return a.string }
        if let u = v as? URL { return u.absoluteString }
        return nil
    }

    static func bool(_ v: AnyObject?) -> Bool? {
        guard let v else { return nil }
        if let n = v as? NSNumber { return n.boolValue }
        return nil
    }

    static func scalar(_ v: AnyObject?) -> AXScalar? {
        guard let v else { return nil }
        if CFGetTypeID(v) == CFBooleanGetTypeID(), let n = v as? NSNumber { return .bool(n.boolValue) }
        if let n = v as? NSNumber { return .number(n.doubleValue) }
        if let s = string(v) { return .string(s) }
        return nil
    }

    static func element(_ v: AnyObject?) -> AXUIElement? {
        guard let v, CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return (v as! AXUIElement)
    }

    static func elements(_ v: AnyObject?) -> [AXUIElement] {
        guard let v, let arr = v as? [AnyObject] else { return [] }
        return arr.compactMap { element($0) }
    }

    static func point(_ v: AnyObject?) -> CGPoint? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var p = CGPoint.zero
        return AXValueGetValue(v as! AXValue, .cgPoint, &p) ? p : nil
    }

    static func size(_ v: AnyObject?) -> CGSize? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var s = CGSize.zero
        return AXValueGetValue(v as! AXValue, .cgSize, &s) ? s : nil
    }

    static func frame(_ el: AXUIElement) -> CGRect? {
        guard case .ok(let a) = attributes(el, ["AXPosition", "AXSize"]),
              let p = point(a["AXPosition"]), let s = size(a["AXSize"]) else { return nil }
        return CGRect(origin: p, size: s)
    }

    static func role(_ el: AXUIElement) -> String? { string(attribute(el, "AXRole")) }

    /// Union of all active displays, in the same top-left global coordinates as AX.
    static func displaysFrame() -> CGRect? {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0 else { return nil }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &ids, &count) == .success else { return nil }
        return ids.prefix(Int(count)).map { CGDisplayBounds($0) }.reduce(CGRect.null) { $0.union($1) }
    }

    /// Reads one element into an AXNode (without children) plus its child references.
    static func readNode(_ el: AXUIElement, focused: AXUIElement?) -> (AXNode, [AXUIElement])? {
        var error: AXError?
        return readNode(el, focused: focused, error: &error)
    }

    /// Same, reporting why the read failed when it returns nil.
    static func readNode(_ el: AXUIElement, focused: AXUIElement?, error: inout AXError?,
                         document: Bool = false) -> (AXNode, [AXUIElement])? {
        var errors: [String: AXError] = [:]
        let names = document ? nodeAttributes + ["AXHeadingLevel", "AXARIARole"] : nodeAttributes
        let result = attributes(el, names, errors: &errors)
        if case .failed(let e) = result { error = e }
        guard case .ok(let a) = result else { return nil }
        var n = AXNode(string(a["AXRole"]) ?? "AXUnknown")
        n.subrole = string(a["AXSubrole"])
        n.ariaRole = string(a["AXARIARole"])
        n.headingLevel = (a["AXHeadingLevel"] as? NSNumber)?.intValue
        if document, n.axRole == "AXHeading", n.headingLevel == nil {
            n.headingLevel = (a["AXValue"] as? NSNumber)?.intValue
        }
        n.title = string(a["AXTitle"])
        n.desc = string(a["AXDescription"])
        n.help = string(a["AXHelp"])
        n.identifier = string(a["AXIdentifier"])
        n.placeholder = string(a["AXPlaceholderValue"])
        n.value = n.isSecure ? nil : scalar(a["AXValue"])
        n.enabled = bool(a["AXEnabled"]) ?? true
        n.selected = bool(a["AXSelected"]) ?? false
        n.expanded = bool(a["AXExpanded"])
        // Some apps (Finder, Notes) report AXFocused=true on every cell of a focused table, so the app's
        // AXFocusedUIElement is the source of truth; the attribute is used only when that is unavailable.
        if let focused { n.focused = CFEqual(focused, el) } else { n.focused = bool(a["AXFocused"]) ?? false }
        if let p = point(a["AXPosition"]), let s = size(a["AXSize"]) { n.frame = CGRect(origin: p, size: s) }
        if Labels.clean(n.title) == nil, Labels.clean(n.desc) == nil, let t = element(a["AXTitleUIElement"]),
           case .ok(let ta) = attributes(t, ["AXValue", "AXTitle", "AXDescription"]) {
            n.titleElementText = string(ta["AXValue"]) ?? string(ta["AXTitle"]) ?? string(ta["AXDescription"])
        }
        var childRefs = elements(a["AXChildren"])
        // A web view that is still building its accessibility tree (Mail right after a message is selected) can
        // time out on AXChildren inside the batched read; the batch then silently reports no children.
        if a["AXChildren"] == nil, isTransient(errors["AXChildren"]) { childRefs = children(el).refs }
        return (n, childRefs)
    }
}

/// Walks an AX subtree into AXNodes with the snapshot's limits: wall-clock deadline, node budget, raw depth,
/// offscreen pruning, and at most `rowCap` visible rows per list.
final class AXWalker {
    let deadline: Date
    let maxNodes: Int
    let maxDepth: Int
    let prune: Bool
    let rowCap: Int
    let focused: AXUIElement?
    let document: Bool
    private(set) var refs: [AXUIElement] = []
    private(set) var truncated = false
    private(set) var nodeCount = 0

    static let listRoles: Set<String> = ["AXTable", "AXOutline", "AXList", "AXGrid", "AXBrowser"]
    static let clipRoles: Set<String> = ["AXScrollArea", "AXWebArea", "AXSplitGroup"]
    /// Roles whose children are never useful to walk.
    static let leafRoles: Set<String> = ["AXStaticText", "AXImage", "AXTextArea", "AXTextField", "AXSlider",
                                         "AXValueIndicator", "AXScrollBar", "AXProgressIndicator", "AXIncrementor"]

    init(deadline: Date, maxNodes: Int, maxDepth: Int, prune: Bool, rowCap: Int = SnapshotBuilder.rowCap,
         focused: AXUIElement?, document: Bool = false) {
        self.deadline = deadline
        self.maxNodes = maxNodes
        self.maxDepth = maxDepth
        self.prune = prune
        self.rowCap = rowCap
        self.focused = focused
        self.document = document
    }

    func walk(_ el: AXUIElement, depth: Int = 0, clip: CGRect?) -> AXNode? {
        if nodeCount >= maxNodes || Date() >= deadline {
            truncated = true
            return nil
        }
        nodeCount += 1
        var readError: AXError?
        var result = AX.readNode(el, focused: focused, error: &readError, document: document)
        if result == nil, AX.isTransient(readError) {
            // The app did not answer in time (a web view building its tree, a busy main thread). Retry once if the
            // deadline allows a full messaging timeout; otherwise report the snapshot as incomplete.
            if Date().addingTimeInterval(Double(AX.messagingTimeout) + 0.05) < deadline {
                result = AX.readNode(el, focused: focused, error: &readError, document: document)
            }
            if result == nil { truncated = true }
        }
        guard let (read, firstRefs) = result else { return nil }
        var node = read
        if prune, let f = node.frame, let c = clip, f.width > 0, f.height > 0, !f.intersects(c) { return nil }
        var childRefs = firstRefs
        if childRefs.isEmpty, node.axRole == "AXWebArea", !node.isZeroSize {
            childRefs = waitForWebContent(el)
        }
        if document, childRefs.isEmpty, ["AXTable", "AXGrid"].contains(node.axRole) {
            childRefs = AX.elements(AX.attribute(el, "AXRows"))
        }
        node.ref = refs.count
        refs.append(el)

        if node.isSecure || RoleMap.dropped.contains(node.axRole) || Self.leafRoles.contains(node.axRole) { return node }
        if depth >= maxDepth {
            if !childRefs.isEmpty { truncated = true }
            return node
        }

        var childClip = clip
        if prune, Self.clipRoles.contains(node.axRole), let f = node.frame, f.width > 0, f.height > 0 {
            childClip = clip.map { $0.intersection(f) } ?? f
        }

        if Self.listRoles.contains(node.axRole) && !document {
            walkList(el, &node, childRefs: childRefs, depth: depth, clip: childClip)
        } else {
            for c in childRefs {
                // Closed submenus hang off menu items; never descend into them.
                if node.axRole == "AXMenuItem" { break }
                if let child = walk(c, depth: depth + 1, clip: childClip) { node.children.append(child) }
                if truncated && (nodeCount >= maxNodes || Date() >= deadline) { break }
            }
        }
        return node
    }

    /// Total time one walk may spend waiting for web areas to populate.
    static let webWaitBudget: TimeInterval = 0.2
    private var webWaited: TimeInterval = 0

    /// A visible web area with no children is usually a page whose accessibility tree WebKit is still building
    /// (it starts on the first AX request, e.g. right after Mail shows a new message). Re-ask a few times.
    private func waitForWebContent(_ el: AXUIElement) -> [AXUIElement] {
        let step: TimeInterval = 0.04
        while webWaited + step <= Self.webWaitBudget, Date().addingTimeInterval(step + 0.1) < deadline {
            Thread.sleep(forTimeInterval: step)
            webWaited += step
            let refs = AX.children(el).refs
            if !refs.isEmpty { return refs }
        }
        return []
    }

    private func walkList(_ el: AXUIElement, _ node: inout AXNode, childRefs: [AXUIElement], depth: Int, clip: CGRect?) {
        var rowCount: CFIndex = 0
        let hasRows = AXUIElementGetAttributeValueCount(el, "AXRows" as CFString, &rowCount) == .success && rowCount > 0
        var candidates = childRefs
        if hasRows {
            node.totalRows = rowCount
            let visible = AX.elements(AX.attribute(el, "AXVisibleRows"))
            if !visible.isEmpty {
                candidates = visible
            } else {
                var rows: CFArray?
                if AXUIElementCopyAttributeValues(el, "AXRows" as CFString, 0, min(rowCount, 400), &rows) == .success {
                    candidates = AX.elements(rows as AnyObject?)
                }
            }
        }
        var kept = 0
        for c in candidates {
            if kept >= rowCap { break }
            if let child = walk(c, depth: depth + 1, clip: clip) {
                node.children.append(child)
                if !RoleMap.dropped.contains(child.axRole) && child.axRole != "AXColumn" { kept += 1 }
            }
            if nodeCount >= maxNodes || Date() >= deadline { break }
        }
        if !hasRows { node.totalRows = max(childRefs.count, node.children.count) }
    }
}
