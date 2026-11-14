// Owner: Mac owner (H3). Typing, key chords, scrolling, and opening apps. Thread-agnostic.
import AppKit
import Foundation

final class InputService: InputProviding, @unchecked Sendable {
    static let chunkSize = 20
    private let lock = NSLock()
    private var installedCache: (date: Date, apps: [String: URL])?

    // MARK: Typing

    /// Splits text into chunks of at most `size` UTF-16 units without breaking a character.
    static func chunks(_ text: String, size: Int = chunkSize) -> [[UniChar]] {
        var out: [[UniChar]] = []
        var cur: [UniChar] = []
        for ch in text {
            let units = Array(String(ch).utf16)
            if cur.count + units.count > size, !cur.isEmpty {
                out.append(cur)
                cur = []
            }
            cur.append(contentsOf: units)
        }
        if !cur.isEmpty { out.append(cur) }
        return out
    }

    func type(text: String) async throws -> Int {
        guard AXIsProcessTrusted() else {
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        }
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        let lines = normalized.components(separatedBy: "\n")
        let returnKey = try KeyChord.parse("return")
        for (i, line) in lines.enumerated() {
            if i > 0 {
                try SyntheticEvents.key(code: returnKey.keyCode, flags: [])
                SyntheticEvents.pause(10)
            }
            for chunk in Self.chunks(line) {
                try SyntheticEvents.typeChunk(chunk)
                SyntheticEvents.pause(12)
            }
        }
        return text.count
    }

    // MARK: Keys

    func key(_ keys: String) async throws {
        let chord = try KeyChord.parse(keys)
        guard AXIsProcessTrusted() else {
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        }
        try SyntheticEvents.key(code: chord.keyCode, flags: chord.flags)
    }

    // MARK: Scroll

    /// Pixel deltas (dx, dy) for a direction and amount. `page` is ~85% of `pageHeight` (or width).
    static func scrollDelta(direction: String, amount: String, page: CGSize) throws -> (dx: Int, dy: Int) {
        let a = amount.trimmingCharacters(in: .whitespaces).lowercased()
        let horizontal = direction == "left" || direction == "right"
        let pixels: Int
        if a == "page" || a.isEmpty {
            pixels = Int(((horizontal ? page.width : page.height) * 0.85).rounded())
        } else if let n = Int(a), n > 0 {
            pixels = min(n, 20000)
        } else {
            throw HelperError(.badRequest, "amount must be 'page' or a positive pixel count")
        }
        switch direction {
        case "up": return (0, pixels)
        case "down": return (0, -pixels)
        case "left": return (pixels, 0)
        case "right": return (-pixels, 0)
        default: throw HelperError(.badRequest, "direction must be up, down, left or right")
        }
    }

    func scroll(direction: String, amount: String, at point: CGPoint?) async throws {
        guard AXIsProcessTrusted() else {
            throw HelperError(.permissionDenied, "Accessibility permission is not granted to the Beacon Helper")
        }
        let target = point ?? Self.frontmostWindowCentre()
        var page = CGDisplayBounds(CGMainDisplayID()).size
        if let target, let display = Self.displayBounds(containing: target) { page = display.size }
        if let w = Self.frontmostWindowFrame(), point == nil || w.contains(point!) {
            page = CGSize(width: min(page.width, w.width), height: min(page.height, w.height))
        }
        if page.width <= 0 || page.height <= 0 { page = CGSize(width: 800, height: 600) }
        let (dx, dy) = try Self.scrollDelta(direction: direction, amount: amount, page: page)
        if let target {
            SyntheticEvents.moveMouse(to: target)
            SyntheticEvents.pause(20)
        }
        // Several small steps scroll more reliably than one large delta.
        let total = max(abs(dx), abs(dy))
        let steps = max(1, Int((Double(total) / 120).rounded(.up)))
        var sentX = 0, sentY = 0
        for i in 1...steps {
            let nx = dx * i / steps, ny = dy * i / steps
            try SyntheticEvents.scroll(dx: Int32(nx - sentX), dy: Int32(ny - sentY), at: target)
            sentX = nx
            sentY = ny
            SyntheticEvents.pause(8)
        }
    }

    static func displayBounds(containing p: CGPoint) -> CGRect? {
        var count: UInt32 = 0
        var id: CGDirectDisplayID = 0
        guard CGGetDisplaysWithPoint(p, 1, &id, &count) == .success, count > 0 else { return nil }
        return CGDisplayBounds(id)
    }

    /// Frame of the frontmost app's front window (not ours), top-left origin.
    static func frontmostWindowFrame() -> CGRect? {
        let own = getpid()
        let frontPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] else { return nil }
        let windows = list.filter { ($0[kCGWindowLayer as String] as? Int) == 0 && ($0[kCGWindowOwnerPID as String] as? pid_t) != own }
        let pick = windows.first { ($0[kCGWindowOwnerPID as String] as? pid_t) == frontPID } ?? windows.first
        guard let b = pick?[kCGWindowBounds as String] as? NSDictionary,
              let rect = CGRect(dictionaryRepresentation: b as CFDictionary) else { return nil }
        return rect
    }

    static func frontmostWindowCentre() -> CGPoint? {
        frontmostWindowFrame().map { CGPoint(x: $0.midX, y: $0.midY) }
    }

    // MARK: Apps

    static let appFolders: [URL] = [
        URL(fileURLWithPath: "/Applications"),
        URL(fileURLWithPath: "/Applications/Utilities"),
        URL(fileURLWithPath: "/System/Applications"),
        URL(fileURLWithPath: "/System/Applications/Utilities"),
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications"),
    ]

    /// Installed apps by display name (cached for 60 s).
    func installedApps() -> [String: URL] {
        if let c = lock.withLock({ installedCache }), Date().timeIntervalSince(c.date) < 60 { return c.apps }
        var apps: [String: URL] = [:]
        let fm = FileManager.default
        for folder in Self.appFolders {
            guard let items = try? fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil,
                                                          options: [.skipsHiddenFiles]) else { continue }
            for url in items where url.pathExtension == "app" {
                let name = url.deletingPathExtension().lastPathComponent
                if apps[name] == nil { apps[name] = url }
            }
        }
        // Finder lives in CoreServices but people ask for it.
        let finder = URL(fileURLWithPath: "/System/Library/CoreServices/Finder.app")
        if apps["Finder"] == nil, fm.fileExists(atPath: finder.path) { apps["Finder"] = finder }
        lock.withLock { installedCache = (Date(), apps) }
        return apps
    }

    static func runningRegularApps() -> [NSRunningApplication] {
        let own = getpid()
        return NSWorkspace.shared.runningApplications.filter {
            $0.activationPolicy == .regular && $0.processIdentifier != own && !$0.isTerminated && $0.localizedName != nil
        }
    }

    func openApp(name: String) async throws -> (app: String, pid: Int32) {
        let query = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { throw HelperError(.badRequest, "name must not be empty") }
        let running = Self.runningRegularApps()
        let installed = installedApps()
        var candidates = running.map { AppMatcher.Candidate(name: $0.localizedName!, running: true) }
        let runningNames = Set(candidates.map(\.name))
        candidates += installed.keys.sorted().filter { !runningNames.contains($0) }
            .map { AppMatcher.Candidate(name: $0, running: false) }

        let (best, close) = AppMatcher.match(query, in: candidates)
        guard let best else {
            throw HelperError(.appNotFound, "no app called '\(query)'", extra: ["close_matches": close])
        }

        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        if best.running, let app = running.first(where: { $0.localizedName == best.name }) {
            if let url = app.bundleURL {
                // Re-opening a running app activates it and reopens a window if it has none.
                if let opened = try? await NSWorkspace.shared.openApplication(at: url, configuration: config) {
                    return (best.name, opened.processIdentifier)
                }
            }
            app.activate()
            return (best.name, app.processIdentifier)
        }
        guard let url = installed[best.name] else {
            throw HelperError(.appNotFound, "no app called '\(query)'", extra: ["close_matches": close])
        }
        do {
            let app = try await NSWorkspace.shared.openApplication(at: url, configuration: config)
            return (app.localizedName ?? best.name, app.processIdentifier)
        } catch {
            throw HelperError(.actionFailed, "could not open \(best.name): \(error.localizedDescription)")
        }
    }

    func listApps() async -> (running: [String], installed: [String]) {
        let running = Array(Set(Self.runningRegularApps().compactMap(\.localizedName))).sorted()
        let installed = installedApps().keys.sorted()
        return (running, installed)
    }
}
