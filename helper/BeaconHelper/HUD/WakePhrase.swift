// Owner: Mac owner. "Hey Beacon" detection, used to bring back a minimized HUD.
import Foundation

enum WakePhrase: Equatable {
    /// What Beacon says when the user says only the wake phrase.
    static let greeting = "Hey, how can I help you?"

    /// The utterance was only the wake phrase ("Hey Beacon.").
    case only
    /// The wake phrase followed by a request ("Hey Beacon, open Mail" gives "open Mail").
    case followedBy(String)

    private static let greetings: Set<String> = ["hey", "hi", "hello", "ok", "okay", "yo"]
    /// "Beacon" plus the spellings Whisper tends to produce for it.
    private static let names: Set<String> = ["beacon", "beacons", "beekon", "beakon", "beckon"]

    /// Matches a transcript that starts with an optional greeting and then "Beacon". Returns nil otherwise.
    static func match(_ text: String) -> WakePhrase? {
        let words = wordRanges(in: text)
        guard !words.isEmpty else { return nil }
        var index = 0
        if greetings.contains(text[words[0]].lowercased()) { index = 1 }
        guard index < words.count, names.contains(text[words[index]].lowercased()) else { return nil }
        let rest = text[words[index].upperBound...]
            .drop(while: { !$0.isLetter && !$0.isNumber })
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return rest.isEmpty ? .only : .followedBy(rest)
    }

    private static func wordRanges(in text: String) -> [Range<String.Index>] {
        var ranges: [Range<String.Index>] = []
        var start: String.Index?
        var i = text.startIndex
        while i < text.endIndex {
            let c = text[i]
            let isWord = c.isLetter || c.isNumber || c == "'"
            if isWord, start == nil { start = i }
            if !isWord, let s = start { ranges.append(s..<i); start = nil }
            i = text.index(after: i)
        }
        if let s = start { ranges.append(s..<text.endIndex) }
        return ranges
    }
}
