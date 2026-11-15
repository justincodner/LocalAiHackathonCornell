import AppKit
import ApplicationServices
import CryptoKit
import ScreenCaptureKit

enum PageReader {
    static func webArea(in window: AXUIElement) -> AXUIElement? {
        let deadline = Date().addingTimeInterval(0.6)
        var pending = [window]
        var examined = 0
        while let element = pending.popLast(), examined < 600, Date() < deadline {
            examined += 1
            guard case .ok(let a) = AX.attributes(element, ["AXRole", "AXChildren", "AXPosition", "AXSize"]) else {
                continue
            }
            let role = AX.string(a["AXRole"])
            if role == "AXWebArea" {
                if let size = AX.size(a["AXSize"]), size.width > 0, size.height > 0 { return element }
            } else if !["AXToolbar", "AXTabGroup", "AXMenuBar", "AXMenu"].contains(role ?? "") {
                pending.append(contentsOf: AX.elements(a["AXChildren"]).reversed())
            }
        }
        return nil
    }

    static func blocks(in tree: AXNode) -> [JSONObject] {
        let containers = ["table", "row", "cell", "columnheader", "rowheader"]
        let readable = Set(containers + ["heading", "text", "image"]).union(RoleMap.interactive)
        var blocks: [JSONObject] = []
        func visit(_ node: AXNode, depth: Int) {
            if ["AXToolbar", "AXMenu", "AXMenuBar", "AXColumn"].contains(node.axRole) { return }
            let role: String
            switch (node.axRole, node.ariaRole) {
            case (_, "columnheader"): role = "columnheader"
            case (_, "rowheader"): role = "rowheader"
            case ("AXTable", _), ("AXGrid", _): role = "table"
            default: role = RoleMap.shortRole(axRole: node.axRole, subrole: node.subrole)
            }
            let mayHaveChildren = !node.children.isEmpty &&
                ["row", "cell", "rowheader", "columnheader"].contains(role)
            let label: String
            if mayHaveChildren {
                label = ""
            } else if role == "text" {
                label = node.value?.text ?? node.title ?? node.desc ?? ""
            } else {
                label = Labels.label(for: node, role: role)?.0 ?? ""
            }
            if readable.contains(role), !label.isEmpty || containers.contains(role) || RoleMap.valued.contains(role) {
                var block: JSONObject = ["role": role, "depth": depth, "text": label]
                if let level = node.headingLevel, role == "heading", level > 0 { block["level"] = level }
                if RoleMap.valued.contains(role) {
                    if node.isSecure {
                        block["value"] = "(hidden)"
                    } else if ["textfield", "textarea", "popup", "slider"].contains(role) {
                        block["value"] = node.value?.text ?? (role == "textfield" || role == "textarea" ? "" : nil)
                    } else {
                        block["value"] = Labels.value(for: node, role: role)
                    }
                }
                blocks.append(block)
            }
            if node.isSecure || RoleMap.valued.contains(role) { return }
            for child in node.children {
                if ["heading", "link", "button"].contains(role), child.axRole == "AXStaticText",
                   let text = Labels.clean(child.value?.text ?? child.title), text == Labels.clean(label) { continue }
                visit(child, depth: depth + 1)
            }
        }
        visit(tree, depth: 0)
        return blocks
    }

    static func hasText(_ blocks: [JSONObject]) -> Bool {
        blocks.contains {
            ["text", "cell", "textarea"].contains($0["role"] as? String ?? "") &&
                (Labels.clean($0["text"] as? String) != nil || Labels.clean($0["value"] as? String) != nil)
        }
    }

    static func ocr(pid: pid_t, window: CGRect, viewport: CGRect) async -> [JSONObject] {
        guard CGPreflightScreenCaptureAccess(),
              let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true),
              let match = content.windows.first(where: {
                  $0.owningApplication?.processID == pid && $0.windowLayer == 0 &&
                  abs($0.frame.minX - window.minX) < 3 && abs($0.frame.minY - window.minY) < 3 &&
                  abs($0.frame.width - window.width) < 3 && abs($0.frame.height - window.height) < 3
              }) else { return [] }
        let visible = viewport.intersection(match.frame)
        guard !visible.isNull, visible.width >= 1, visible.height >= 1 else { return [] }
        let filter = SCContentFilter(desktopIndependentWindow: match)
        let config = SCStreamConfiguration()
        config.ignoreShadowsSingleWindow = true
        config.showsCursor = false
        let scale = CGFloat(filter.pointPixelScale)
        config.width = max(1, Int(match.frame.width * scale))
        config.height = max(1, Int(match.frame.height * scale))
        guard let image = try? await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        else { return [] }
        let crop = CGRect(x: (visible.minX - match.frame.minX) * scale,
                          y: (visible.minY - match.frame.minY) * scale,
                          width: visible.width * scale, height: visible.height * scale).integral
        let lines = SelectionFocus.readingOrder(await RegionReader.recognizePage(image.cropping(to: crop) ?? image))
        return lines.split(separator: "\n").map { ["role": "text", "depth": 0, "text": String($0)] }
    }
}

extension AccessibilityService {
    func page(app: String?, window: String?, direction: String?, pageID: String?,
              input: InputProviding) async throws -> JSONObject {
        let target = try frontmostTarget()
        let focused = try focusedWindow(target.app, appName: target.name)
        let title = Labels.withoutTabNoise(AX.string(AX.attribute(focused, "AXTitle")) ?? "")
        guard (app == nil || target.name == app), (window == nil || title == window) else {
            throw HelperError(.staleSnapshot, "The active window changed")
        }
        let web = PageReader.webArea(in: focused)
        let root = web ?? focused
        let url = AX.string(AX.attribute(root, "AXURL")) ?? ""
        let key = "\(target.pid)\n\(CFHash(focused))\n\(CFHash(root))\n\(title)\n\(url)"
        let identity = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
        let windowFrame = AX.frame(focused) ?? .null
        let viewport = (AX.frame(root) ?? windowFrame).intersection(windowFrame)
        if let pageID, pageID != identity {
            throw HelperError(.staleSnapshot, "The active page changed")
        }
        if let direction {
            guard pageID != nil, NSWorkspace.shared.frontmostApplication?.processIdentifier == target.pid,
                  !viewport.isEmpty, !viewport.isNull else {
                throw HelperError(.staleSnapshot, "The active page changed")
            }
            try await input.scroll(direction: direction, amount: String(max(1, Int(viewport.height * 0.8))),
                                   at: CGPoint(x: viewport.midX, y: viewport.midY))
            try await Task.sleep(for: .milliseconds(280))
            return try await page(app: target.name, window: title, direction: nil, pageID: identity, input: input)
        }

        var blocks: [JSONObject] = []
        var source = "accessibility"
        var partial = true // The native tree can omit unloaded content even if all of its nodes were read.
        if let dom = await BrowserDOM.read(pid: target.pid), !dom.blocks.isEmpty {
            blocks = dom.blocks
            partial = dom.partial
            source = "dom"
        }
        if !PageReader.hasText(blocks), web != nil || BrowserDOM.bundleID(pid: target.pid) == nil {
            let walker = AXWalker(deadline: Date().addingTimeInterval(1.6), maxNodes: 8000,
                                  maxDepth: 100, prune: false, focused: nil, document: true)
            if let tree = walker.walk(root, clip: nil) {
                let native = PageReader.blocks(in: tree)
                if blocks.isEmpty || PageReader.hasText(native) {
                    blocks = native
                    source = "accessibility"
                    partial = true
                }
            }
        }
        if !PageReader.hasText(blocks) {
            let lines = await PageReader.ocr(pid: target.pid, window: windowFrame, viewport: viewport)
            if !lines.isEmpty {
                blocks += lines
                source = "ocr"
                partial = true
            }
        }
        let current = try frontmostTarget()
        guard current.pid == target.pid,
              CFEqual(try focusedWindow(current.app, appName: current.name), focused),
              Labels.withoutTabNoise(AX.string(AX.attribute(focused, "AXTitle")) ?? "") == title,
              (AX.string(AX.attribute(root, "AXURL")) ?? "") == url else {
            throw HelperError(.staleSnapshot, "The active page changed")
        }
        return ["page_id": identity, "source": source, "partial": partial, "blocks": blocks]
    }
}
