// Owner: Mac owner (H3). Fuzzy app-name matching for /app/open ("open notes", "system preferences", "safary").
import Foundation

enum AppMatcher {
    struct Candidate: Equatable {
        let name: String
        let running: Bool
    }

    /// Spoken names that differ from the app's real name.
    static let aliases: [String: String] = [
        "systempreferences": "System Settings", "preferences": "System Settings", "settings": "System Settings",
        "browser": "Safari", "webbrowser": "Safari", "internet": "Safari",
        "email": "Mail", "mailapp": "Mail", "appleemail": "Mail",
        "itunes": "Music", "applemusic": "Music", "files": "Finder", "filebrowser": "Finder",
        "texteditor": "TextEdit", "calc": "Calculator", "chrome": "Google Chrome", "googlechrome": "Google Chrome",
        "word": "Microsoft Word", "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
        "vscode": "Visual Studio Code", "code": "Visual Studio Code", "facetime": "FaceTime",
        "imessage": "Messages", "texts": "Messages", "appstore": "App Store",
    ]

    /// Lower-case, no ".app", no "app"/"the"/"application" filler, letters and digits only.
    static func normalize(_ s: String) -> String {
        var t = s.lowercased()
        if t.hasSuffix(".app") { t.removeLast(4) }
        let filler: Set<String> = ["the", "app", "application", "my", "open", "launch"]
        let words = t.split(whereSeparator: { !($0.isLetter || $0.isNumber) }).map(String.init)
        let kept = words.filter { !filler.contains($0) }
        return (kept.isEmpty ? words : kept).joined()
    }

    static func levenshtein(_ a: String, _ b: String) -> Int {
        let a = Array(a), b = Array(b)
        if a.isEmpty { return b.count }
        if b.isEmpty { return a.count }
        var prev = Array(0...b.count)
        var cur = prev
        for i in 1...a.count {
            cur[0] = i
            for j in 1...b.count {
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1))
            }
            swap(&prev, &cur)
        }
        return prev[b.count]
    }

    static func similarity(_ a: String, _ b: String) -> Double {
        let m = max(a.count, b.count)
        return m == 0 ? 1 : 1 - Double(levenshtein(a, b)) / Double(m)
    }

    /// Score 0...100 for a query against an app name.
    static func score(query: String, name: String) -> Double {
        let q = normalize(query), n = normalize(name)
        guard !q.isEmpty, !n.isEmpty else { return 0 }
        if q == n { return 100 }
        if n.hasPrefix(q), q.count >= 2 { return 90 - Double(n.count - q.count) * 0.5 }
        // Word prefix: "code" in "visual studio code", "chrome" in "google chrome".
        let words = name.lowercased().split(whereSeparator: { !($0.isLetter || $0.isNumber) }).map(String.init)
        if words.contains(q) { return 82 }
        if n.contains(q), q.count >= 3 { return 75 - Double(n.count - q.count) * 0.3 }
        if q.contains(n), n.count >= 4 { return 65 }
        let sim = similarity(q, n)
        return sim >= 0.6 ? 40 + sim * 30 : sim * 40
    }

    static let acceptScore = 60.0

    /// 0...1: best similarity between any query word and any name word, or the whole strings.
    static func closeness(query: String, name: String) -> Double {
        func words(_ s: String) -> [String] {
            s.lowercased().split(whereSeparator: { !($0.isLetter || $0.isNumber) }).map(String.init).filter { $0.count >= 3 }
        }
        var best = similarity(normalize(query), normalize(name))
        for q in words(query) {
            for n in words(name) {
                best = max(best, similarity(q, n))
                if n.hasPrefix(q) || q.hasPrefix(n) { best = max(best, 0.7) }
            }
        }
        return best
    }

    /// Best candidate (running apps win ties, then shorter names), and close matches for an error.
    static func match(_ query: String, in candidates: [Candidate]) -> (best: Candidate?, close: [String]) {
        let q = normalize(query)
        if let alias = aliases[q], let c = candidates.first(where: { $0.name.lowercased() == alias.lowercased() }) {
            return (c, [])
        }
        let scored = candidates.map { ($0, score(query: query, name: $0.name)) }
            .sorted { a, b in
                if a.1 != b.1 { return a.1 > b.1 }
                if a.0.running != b.0.running { return a.0.running }
                return a.0.name.count < b.0.name.count
            }
        if let top = scored.first, top.1 >= acceptScore { return (top.0, []) }
        var seen = Set<String>()
        let close = candidates.map { ($0.name, closeness(query: query, name: $0.name)) }
            .filter { $0.1 >= 0.5 }
            .sorted { $0.1 > $1.1 }
            .map(\.0).filter { seen.insert($0).inserted }.prefix(3)
        return (nil, Array(close))
    }
}
