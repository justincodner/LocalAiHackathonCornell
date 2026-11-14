// Owner: Mac owner (H2). Label repair P1 (spec 5.5): build a label for an unlabelled control from its
// identifier (`sendButton` -> "send button"), else from the nearest labelled sibling.
import Foundation

enum LabelRepair {
    /// Turns a developer identifier into words. Returns nil for identifiers that carry no meaning
    /// (AppKit's `_NS:123`, UUIDs, pure numbers, pointer-like hex).
    static func humanize(identifier raw: String?) -> String? {
        guard let raw = raw?.trimmingCharacters(in: .whitespaces), !raw.isEmpty else { return nil }
        if raw.hasPrefix("_NS:") || raw.hasPrefix("_") && raw.dropFirst().allSatisfy({ $0.isNumber || $0 == ":" }) {
            return nil
        }
        if UUID(uuidString: raw) != nil { return nil }
        if raw.range(of: "0x[0-9a-fA-F]{4,}", options: .regularExpression) != nil { return nil }

        var words: [String] = []
        var current = ""
        let chars = Array(raw)
        func flush() {
            if !current.isEmpty { words.append(current) }
            current = ""
        }
        for (i, ch) in chars.enumerated() {
            if !(ch.isLetter || ch.isNumber) { flush(); continue }
            if ch.isUppercase, !current.isEmpty {
                let prev = chars[i - 1]
                let nextIsLower = i + 1 < chars.count && chars[i + 1].isLowercase
                // camelCase boundary, or the end of an acronym run ("URLField" -> URL | Field).
                if prev.isLowercase || prev.isNumber || (prev.isUppercase && nextIsLower) { flush() }
            } else if ch.isNumber, let last = current.last, last.isLetter {
                flush()
            } else if ch.isLetter, let last = current.last, last.isNumber {
                flush()
            }
            current.append(ch)
        }
        flush()
        let letterWords = words.filter { $0.contains(where: { $0.isLetter }) }
        guard !letterWords.isEmpty else { return nil }
        // Drop trailing numeric suffixes such as "button-3".
        let kept = words.reversed().drop(while: { $0.allSatisfy(\.isNumber) }).reversed()
        let phrase = kept.map { $0.lowercased() }.joined(separator: " ")
        return phrase.isEmpty ? nil : phrase
    }

    /// The "near" form of an inferred label.
    static func nearLabel(_ siblingLabel: String) -> String {
        let (short, _) = Labels.trim(siblingLabel, to: 60)
        return "unlabeled, near '\(short)'"
    }

    /// Index of the nearest sibling with a non-empty label, preferring the previous one at equal distance.
    static func nearestLabelled(in labels: [String?], to index: Int) -> Int? {
        var d = 1
        while index - d >= 0 || index + d < labels.count {
            if index - d >= 0, let l = labels[index - d], !l.isEmpty { return index - d }
            if index + d < labels.count, let l = labels[index + d], !l.isEmpty { return index + d }
            d += 1
        }
        return nil
    }
}
