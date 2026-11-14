// Owner: Mac owner (H2). Pure filter pipeline (spec 5.4) and text block (spec 5.2).
// Input: the raw AXNode tree of a window (already pruned of offscreen/zero-size nodes by the walker, and bounded
// by its depth and time limits). Output: numbered elements, the text block lines, and the truncation flag.
import CoreGraphics
import Foundation

struct SnapshotElement {
    var id: Int
    var depth: Int
    var role: String
    var label: String
    var labelSource: String
    var value: String?
    var states: [String]
    var frame: CGRect?
    /// Walker reference index, used to register the AXUIElement in the element table.
    var ref: Int?
    /// Full untruncated text when the element is merged static text (for /ax/read).
    var fullText: String?
    /// For lists: total rows.
    var rowCount: Int?

    var bounds: [Int] {
        guard let f = frame else { return [0, 0, 0, 0] }
        return [Int(f.origin.x.rounded()), Int(f.origin.y.rounded()), Int(f.width.rounded()), Int(f.height.rounded())]
    }

    var json: JSONObject {
        [
            "id": id, "depth": depth, "role": role, "label": label,
            "value": value.map { $0 as Any } ?? NSNull(),
            "states": states, "bounds": bounds, "label_source": labelSource,
        ]
    }
}

enum SnapshotLine {
    case element(SnapshotElement)
    case moreRows(depth: Int, count: Int)
}

struct BuiltSnapshot {
    var elements: [SnapshotElement]
    var lines: [SnapshotLine]
    var truncated: Bool
    var totalElements: Int
    var focusedID: Int? { elements.first(where: { $0.states.contains("focused") })?.id }
}

enum SnapshotBuilder {
    static let maxDepth = 25
    static let rowCap = 20
    static let stateOrder = ["disabled", "focused", "selected", "checked", "expanded", "truncated", "inferred"]

    /// Intermediate filtered node.
    struct Out {
        var role: String
        var label: String = ""
        var labelSource: String = "none"
        var fullLabel: String = ""
        var value: String?
        var states: Set<String> = []
        var frame: CGRect?
        var ref: Int?
        var fullText: String?
        var children: [Out] = []
        var rowCount: Int?
        var moreRows = 0

        var isInteractive: Bool { RoleMap.interactive.contains(role) }
        var count: Int { 1 + children.reduce(0) { $0 + $1.count } }
    }

    /// Builds the snapshot of `root`. When `includeRoot` is false (window, sheet, menu), the root's children are
    /// the top level at depth 0; when true (focused scope), the root itself is element 1.
    static func build(root: AXNode, includeRoot: Bool, maxElements: Int, walkTruncated: Bool = false) -> BuiltSnapshot {
        let top = includeRoot ? finishSiblings(transform(root)) : transformChildren(root.children)
        var elements: [SnapshotElement] = []
        var lines: [SnapshotLine] = []
        var truncated = walkTruncated
        flatten(top, depth: 0, maxElements: max(1, maxElements), elements: &elements, lines: &lines, truncated: &truncated)
        let total = top.reduce(0) { $0 + $1.count }
        return BuiltSnapshot(elements: elements, lines: lines, truncated: truncated, totalElements: total)
    }

    // MARK: Transform

    static func transform(_ n: AXNode) -> [Out] {
        switch RoleMap.kind(axRole: n.axRole, subrole: n.subrole) {
        case .drop:
            return []
        case .transparent:
            return transformChildren(n.children)
        case .role(let role):
            let container = ["group", "list", "toolbar", "window", "menu"].contains(role)
            if n.isZeroSize {
                // Zero-size containers sometimes hold real content (web layout); zero-size controls are hidden.
                return container ? transformChildren(n.children) : []
            }
            var o = makeOut(n, role: role)
            switch role {
            case "group":
                if o.label.isEmpty { return transformChildren(n.children) }
                o.children = transformChildren(n.children)
            case "image":
                if o.label.isEmpty { return [] }   // decorative
            case "text":
                if o.label.isEmpty { return [] }
            case "list":
                o.children = transformChildren(n.children)
                capRows(&o, totalRows: n.totalRows)
            default:
                if RoleMap.atomic.contains(role) {
                    // Text inside buttons, links, rows... is already in the label; keep only nested controls.
                    let keepFields = !(role == "row" || role == "cell")
                    let inner = n.children.flatMap(transform)
                    if o.label.isEmpty, role == "row" || role == "cell" {
                        // Cells labelled by their own title/description rather than static text.
                        let parts = labels(of: inner)
                        if !parts.isEmpty {
                            let full = parts.joined(separator: role == "row" ? " - " : " ")
                            let (short, cut) = Labels.trim(full)
                            o.label = short
                            o.fullLabel = full
                            o.labelSource = "child"
                            if cut { o.states.insert("truncated") }
                        }
                    }
                    o.children = finishSiblings(interactiveOnly(inner, keepFields: keepFields))
                } else {
                    o.children = transformChildren(n.children)
                }
            }
            return [o]
        }
    }

    /// Non-empty labels of a list of outs and their descendants, in order (at most 12).
    static func labels(of outs: [Out]) -> [String] {
        var result: [String] = []
        func visit(_ o: Out) {
            guard result.count < 12 else { return }
            if !o.fullLabel.isEmpty { result.append(o.fullLabel); return }
            o.children.forEach(visit)
        }
        outs.forEach(visit)
        return result
    }

    static func transformChildren(_ children: [AXNode]) -> [Out] {
        finishSiblings(children.flatMap(transform))
    }

    /// Removes duplicate control labels, merges static text, and repairs unlabelled controls.
    static func finishSiblings(_ outs: [Out]) -> [Out] {
        let choices = outs.filter { $0.role == "radio" || $0.role == "checkbox" }
        let distinct = outs.filter { item in
            guard item.role == "text", item.states.isEmpty, let frame = item.frame, !frame.isEmpty else {
                return true
            }
            return !choices.contains { choice in
                choice.fullLabel == item.fullLabel && choice.frame?.contains(frame) == true
            }
        }
        return repairFromSiblings(mergeTexts(distinct))
    }

    static func makeOut(_ n: AXNode, role: String) -> Out {
        var o = Out(role: role, frame: n.frame, ref: n.ref)
        if let (full, source) = Labels.label(for: n, role: role) {
            let (short, cut) = Labels.trim(full)
            o.label = short
            o.fullLabel = full
            o.labelSource = source
            if cut { o.states.insert("truncated") }
        } else if o.isInteractive, let words = LabelRepair.humanize(identifier: n.identifier) {
            o.label = Labels.trim(words).0
            o.fullLabel = words
            o.labelSource = "inferred"
            o.states.insert("inferred")
        }
        if let v = Labels.value(for: n, role: role) {
            let (short, cut) = Labels.trim(v)
            o.value = short
            if cut { o.states.insert("truncated") }
        }
        if !n.enabled { o.states.insert("disabled") }
        if n.focused { o.states.insert("focused") }
        if n.selected { o.states.insert("selected") }
        if Labels.isChecked(n, role: role) { o.states.insert("checked") }
        if n.expanded == true { o.states.insert("expanded") }
        if role == "tab", Labels.isChecked(n, role: "radio") { o.states.insert("selected") }
        return o
    }

    /// Keeps only controls (and anything focused) from a subtree, dropping the wrappers around them.
    static func interactiveOnly(_ outs: [Out], keepFields: Bool) -> [Out] {
        outs.flatMap { o -> [Out] in
            let isField = o.role == "textfield" || o.role == "textarea"
            if o.states.contains("focused") || (o.isInteractive && (keepFields || !isField)) { return [o] }
            return interactiveOnly(o.children, keepFields: keepFields)
        }
    }

    static func mergeTexts(_ outs: [Out]) -> [Out] {
        var result: [Out] = []
        for o in outs {
            if o.role == "text", o.children.isEmpty, var last = result.last, last.role == "text", last.children.isEmpty,
               adjacent(last.frame, o.frame) {
                let full = (last.fullText ?? last.fullLabel) + " " + (o.fullText ?? o.fullLabel)
                let (short, cut) = Labels.trim(full)
                last.label = short
                last.fullLabel = full
                last.fullText = full
                if cut { last.states.insert("truncated") }
                last.states.formUnion(o.states.subtracting(["truncated"]))
                if let a = last.frame, let b = o.frame { last.frame = a.union(b) } else { last.frame = last.frame ?? o.frame }
                result[result.count - 1] = last
            } else {
                result.append(o)
            }
        }
        return result
    }

    /// Two text runs are adjacent if they share a line or the vertical gap between them is small. Text that only
    /// became neighbours because unlabelled groups were collapsed (e.g. a footer and a fixed pay bar) stays separate.
    static func adjacent(_ a: CGRect?, _ b: CGRect?) -> Bool {
        guard let a, let b, !a.isEmpty, !b.isEmpty else { return true }
        let gap = max(b.minY - a.maxY, a.minY - b.maxY)
        return gap <= 16
    }

    static func repairFromSiblings(_ outs: [Out]) -> [Out] {
        var result = outs
        let labels: [String?] = outs.map { $0.label.isEmpty ? nil : $0.label }
        for i in result.indices where result[i].isInteractive && result[i].label.isEmpty {
            guard let j = LabelRepair.nearestLabelled(in: labels, to: i), let near = labels[j] else { continue }
            let text = LabelRepair.nearLabel(near)
            result[i].label = Labels.trim(text).0
            result[i].fullLabel = text
            result[i].labelSource = "inferred"
            result[i].states.insert("inferred")
        }
        return result
    }

    /// Lists and tables keep the first 20 rows (spec 5.4 step 6).
    static func capRows(_ o: inout Out, totalRows: Int?) {
        let rowIdx = o.children.indices.filter { o.children[$0].role == "row" }
        if !rowIdx.isEmpty {
            let keep = Set(rowIdx.prefix(rowCap))
            o.children = o.children.indices.compactMap { i in
                (o.children[i].role != "row" || keep.contains(i)) ? o.children[i] : nil
            }
            let total = max(totalRows ?? rowIdx.count, rowIdx.count)
            o.rowCount = total
            o.moreRows = total - keep.count
        } else {
            let shown = o.children.count
            let total = max(totalRows ?? shown, shown)
            if shown > rowCap { o.children = Array(o.children.prefix(rowCap)) }
            o.rowCount = total
            o.moreRows = total - min(shown, rowCap)
        }
    }

    // MARK: Flatten

    static func flatten(_ outs: [Out], depth: Int, maxElements: Int, elements: inout [SnapshotElement],
                        lines: inout [SnapshotLine], truncated: inout Bool) {
        for o in outs {
            if depth > maxDepth { truncated = true; return }
            if elements.count >= maxElements { truncated = true; return }
            let el = SnapshotElement(id: elements.count + 1, depth: depth, role: o.role, label: o.label,
                                     labelSource: o.label.isEmpty ? "none" : o.labelSource, value: o.value,
                                     states: stateOrder.filter { o.states.contains($0) }, frame: o.frame, ref: o.ref,
                                     fullText: o.fullText, rowCount: o.rowCount)
            elements.append(el)
            lines.append(.element(el))
            flatten(o.children, depth: depth + 1, maxElements: maxElements, elements: &elements, lines: &lines,
                    truncated: &truncated)
            if o.moreRows > 0 {
                lines.append(.moreRows(depth: min(depth + 1, maxDepth), count: o.moreRows))
            }
        }
    }
}

// MARK: - Text block (spec 5.2)

enum TextBlock {
    static func quote(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    static func header(app: String, window: String, focusedID: Int?) -> String {
        "APP \(app) | WINDOW \(quote(window)) | FOCUS \(focusedID.map { "[\($0)]" } ?? "none")"
    }

    static func line(_ e: SnapshotElement) -> String {
        var s = String(repeating: "  ", count: e.depth) + "[\(e.id)] \(e.role)"
        if !e.label.isEmpty { s += " " + quote(e.label) }
        if let n = e.rowCount { s += " \(n) row" + (n == 1 ? "" : "s") }
        if let v = e.value { s += " value=" + quote(v) }
        if !e.states.isEmpty { s += " (" + e.states.joined(separator: ", ") + ")" }
        return s
    }

    static func render(app: String, window: String, focusedID: Int?, lines: [SnapshotLine]) -> String {
        var out = [header(app: app, window: window, focusedID: focusedID)]
        for l in lines {
            switch l {
            case .element(let e): out.append(line(e))
            case .moreRows(let depth, let count):
                out.append(String(repeating: "  ", count: depth) + "... \(count) more row\(count == 1 ? "" : "s") (scroll or use find)")
            }
        }
        return out.joined(separator: "\n")
    }
}
