// Owner: Mac owner (H2). Pure model of a raw accessibility node, the AX role map (spec 5.3) and label rules.
// Nothing in this file touches the AX API, so it is unit-tested directly.
import CoreGraphics
import Foundation

/// A value read from AXValue, reduced to what the snapshot needs.
enum AXScalar: Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)

    var text: String {
        switch self {
        case .string(let s): return s
        case .number(let d): return d == d.rounded() && abs(d) < 1e15 ? String(Int(d)) : String(d)
        case .bool(let b): return b ? "1" : "0"
        }
    }

    var number: Double? {
        switch self {
        case .number(let d): return d
        case .bool(let b): return b ? 1 : 0
        case .string(let s): return Double(s)
        }
    }
}

/// One element of the raw (unfiltered) accessibility tree, as read by the walker.
struct AXNode {
    var axRole: String
    var subrole: String? = nil
    var title: String? = nil
    var desc: String? = nil
    var value: AXScalar? = nil
    var help: String? = nil
    var identifier: String? = nil
    var placeholder: String? = nil
    /// Text of the linked title element (`AXTitleUIElement`), if any.
    var titleElementText: String? = nil
    var enabled: Bool = true
    var focused: Bool = false
    var selected: Bool = false
    var expanded: Bool? = nil
    var frame: CGRect? = nil
    var children: [AXNode] = []
    /// For list-like containers: the total number of rows (`AXRows` count), including ones not walked.
    var totalRows: Int? = nil
    /// Index into the walker's element-reference array; nil in tests.
    var ref: Int? = nil

    init(_ axRole: String, subrole: String? = nil, title: String? = nil, desc: String? = nil, value: AXScalar? = nil,
         identifier: String? = nil, frame: CGRect? = nil, children: [AXNode] = []) {
        self.axRole = axRole
        self.subrole = subrole
        self.title = title
        self.desc = desc
        self.value = value
        self.identifier = identifier
        self.frame = frame
        self.children = children
    }

    var isSecure: Bool { subrole == "AXSecureTextField" }
    var isZeroSize: Bool { if let f = frame { return f.width <= 0 || f.height <= 0 }; return false }
}

// MARK: - Role map

enum RoleMap {
    /// What the filter does with an AX role.
    enum Kind: Equatable {
        case role(String)   // emit with this short role
        case transparent    // not emitted; its children are
        case drop           // dropped with its subtree
    }

    /// Roles dropped with their subtree (decorative or structural noise).
    static let dropped: Set<String> = [
        "AXSplitter", "AXScrollBar", "AXValueIndicator", "AXGrowArea", "AXColumn", "AXRuler", "AXRulerMarker",
        "AXBusyIndicator", "AXHandle", "AXMatte", "AXRelevanceIndicator", "AXMenuBar",
    ]

    static func kind(axRole: String, subrole: String?) -> Kind {
        if dropped.contains(axRole) { return .drop }
        switch axRole {
        case "AXButton", "AXDisclosureTriangle", "AXColorWell":
            return .role(subrole == "AXTabButton" ? "tab" : "button")
        case "AXMenuButton", "AXPopUpButton": return .role("popup")
        case "AXLink": return .role("link")
        case "AXTextField", "AXComboBox", "AXDateField", "AXTimeField", "AXSearchField": return .role("textfield")
        case "AXTextArea": return .role("textarea")
        case "AXCheckBox": return .role("checkbox")
        case "AXRadioButton": return .role(subrole == "AXTabButton" ? "tab" : "radio")
        case "AXTab": return .role("tab")
        case "AXMenu": return .role("menu")
        case "AXMenuItem", "AXMenuBarItem": return .role("menuitem")
        case "AXList", "AXTable", "AXOutline", "AXGrid", "AXBrowser": return .role("list")
        case "AXRow": return .role("row")
        case "AXCell": return .role("cell")
        case "AXSlider", "AXIncrementor", "AXStepper", "AXLevelIndicator", "AXProgressIndicator":
            return .role("slider")
        case "AXImage": return .role("image")
        case "AXHeading": return .role("heading")
        case "AXStaticText": return .role("text")
        case "AXToolbar": return .role("toolbar")
        case "AXGroup", "AXRadioGroup", "AXTabGroup", "AXWebArea", "AXLayoutArea", "AXLayoutItem", "AXPopover",
             "AXDockItem", "AXApplication":
            return .role("group")
        case "AXWindow", "AXSheet", "AXDrawer", "AXSystemDialog": return .role("window")
        default:
            // AXScrollArea, AXSplitGroup, AXUnknown, AXMatte, anything new: structure without meaning.
            return .transparent
        }
    }

    /// Short role for find results and other places where every node needs a role.
    static func shortRole(axRole: String, subrole: String?) -> String {
        if case .role(let r) = kind(axRole: axRole, subrole: subrole) { return r }
        return "group"
    }

    static let interactive: Set<String> = [
        "button", "link", "textfield", "textarea", "checkbox", "radio", "menuitem", "tab", "slider", "popup",
    ]
    /// Roles whose own descendants' text is folded into their label rather than emitted.
    static let atomic: Set<String> = interactive.union(["heading", "row", "cell", "image"])
    /// Roles that report a `value`.
    static let valued: Set<String> = ["textfield", "textarea", "slider", "checkbox", "radio", "popup"]
}

// MARK: - Labels and values

enum Labels {
    static let maxLength = 80

    /// Title-bar buttons carry no title or description, only a subrole.
    static let windowButtonLabels: [String: String] = [
        "AXCloseButton": "close window", "AXMinimizeButton": "minimize window", "AXZoomButton": "zoom window",
        "AXFullScreenButton": "full screen", "AXToolbarButton": "toolbar",
    ]

    /// Collapses whitespace and newlines to single spaces and trims.
    static func clean(_ s: String?) -> String? {
        guard let s else { return nil }
        let parts = s.split(whereSeparator: { $0.isWhitespace || $0.isNewline })
        let joined = parts.joined(separator: " ")
        return joined.isEmpty ? nil : joined
    }

    /// Trims to 80 characters; returns whether it was cut.
    static func trim(_ s: String, to limit: Int = maxLength) -> (String, Bool) {
        guard s.count > limit else { return (s, false) }
        return (String(s.prefix(limit - 3)) + "...", true)
    }

    /// All static-text (and, optionally, text-field value) strings in a subtree, in order.
    static func descendantTexts(_ node: AXNode, includeFields: Bool, limit: Int = 12) -> [String] {
        var out: [String] = []
        func visit(_ n: AXNode) {
            guard out.count < limit else { return }
            if n.axRole == "AXStaticText" {
                if let t = clean(n.value?.text) ?? clean(n.title) ?? clean(n.desc) { out.append(t) }
                return
            }
            if includeFields, n.axRole == "AXTextField", !n.isSecure, let t = clean(n.value?.text) {
                out.append(t)
                return
            }
            for c in n.children { visit(c) }
        }
        for c in node.children { visit(c) }
        return out
    }

    static func firstDescendantImageLabel(_ node: AXNode) -> String? {
        for c in node.children {
            if c.axRole == "AXImage", let d = clean(c.desc) ?? clean(c.title) { return d }
            if let d = firstDescendantImageLabel(c) { return d }
        }
        return nil
    }

    /// Label rule of spec 5.3: title, description, linked title element, help, first static-text child.
    /// Returns the untrimmed label and its source.
    static func label(for node: AXNode, role: String) -> (String, String)? {
        guard let (label, source) = rawLabel(for: node, role: role) else { return nil }
        // Chrome's memory note can reach a tab through its title, description or a child text,
        // and some builds expose tabs as plain buttons, so it is dropped from every label.
        let cleaned = withoutTabNoise(label)
        return (role == "tab" ? withoutUnreadCount(cleaned) : cleaned, source)
    }

    /// Chrome appends "- Memory usage - 165 MB" to tab titles: tokens the model pays for and
    /// words Beacon would read aloud, with nothing the user asked about. Any dash (or a colon)
    /// and non-breaking spaces are allowed around it.
    static func withoutTabNoise(_ label: String) -> String {
        let cleaned = label.replacingOccurrences(
            of: #"[\s\u00A0]*[-\u2010-\u2015:|,][\s\u00A0]*(?:high[\s\u00A0]+)?memory[\s\u00A0]+usage[\s\u00A0]*[-\u2010-\u2015:][\s\u00A0]*[0-9.,]+[\s\u00A0]*[KMGT]B\b"#,
            with: "", options: [.regularExpression, .caseInsensitive])
            .trimmingCharacters(in: .whitespaces)
        return cleaned.isEmpty ? label : cleaned
    }

    /// "(8623) Discord | @Sam": the unread counter web apps put in front of a tab title.
    static func withoutUnreadCount(_ label: String) -> String {
        let cleaned = label.replacingOccurrences(of: #"^\(\d[\d,]*\+?\)\s+"#, with: "", options: .regularExpression)
        return cleaned.isEmpty ? label : cleaned
    }

    private static func rawLabel(for node: AXNode, role: String) -> (String, String)? {
        if role == "text" {
            if let v = clean(node.value?.text) ?? clean(node.title) ?? clean(node.desc) { return (v, "title") }
            return nil
        }
        // Popups and checkboxes often carry their state in `title`; still the best label available.
        if let t = clean(node.title) { return (t, "title") }
        if let d = clean(node.desc) { return (d, "description") }
        if let t = clean(node.titleElementText) { return (t, "title") }
        if let w = windowButtonLabels[node.subrole ?? ""] { return (w, "description") }
        if let h = clean(node.help) { return (h, "help") }
        if ["textfield", "textarea"].contains(role), let p = clean(node.placeholder) { return (p, "description") }
        guard RoleMap.atomic.contains(role) else { return nil }
        let texts = descendantTexts(node, includeFields: role == "row" || role == "cell")
        if !texts.isEmpty {
            return (texts.joined(separator: role == "row" ? " - " : " "), "child")
        }
        if let img = firstDescendantImageLabel(node) { return (img, "child") }
        return nil
    }

    /// The `value` field: inputs, sliders and checkboxes only. Secure fields always "(hidden)".
    static func value(for node: AXNode, role: String) -> String? {
        guard RoleMap.valued.contains(role) else { return nil }
        if node.isSecure { return "(hidden)" }
        switch role {
        case "checkbox", "radio":
            guard let n = node.value?.number else { return nil }
            return n >= 2 ? "mixed" : (n >= 1 ? "on" : "off")
        case "textfield", "textarea":
            guard let v = node.value else { return "" }
            return clean(v.text) ?? ""
        default:
            return node.value.flatMap { clean($0.text) }
        }
    }

    /// Whether the node is checked (checkbox, radio, switch).
    static func isChecked(_ node: AXNode, role: String) -> Bool {
        guard role == "checkbox" || role == "radio" else { return false }
        return (node.value?.number ?? 0) >= 1
    }
}
