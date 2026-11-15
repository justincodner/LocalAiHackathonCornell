// Owner: Mac owner. Greeting / "Hey Beacon" detection: wakes the HUD and is answered in kind, with no model call.
import Foundation

enum WakePhrase: Equatable {
    /// The utterance was only a greeting and/or "Beacon" ("Hey Beacon.", "What's up?", "Good morning").
    case only
    /// A wake phrase followed by a request ("Hey Beacon, open Mail" gives "open Mail").
    case followedBy(String)

    /// Greetings as heard (apostrophes dropped, lowercased) and how Beacon says them back. Longest first.
    private static let greetingPhrases: [(words: [String], reply: String)] = [
        (["whats", "up"], "What's up"), (["what", "up"], "What's up"), (["whats", "good"], "What's good"),
        (["good", "morning"], "Good morning"), (["good", "afternoon"], "Good afternoon"),
        (["good", "evening"], "Good evening"), (["good", "day"], "Good day"),
        (["hey", "there"], "Hey there"), (["hi", "there"], "Hi there"), (["hello", "there"], "Hello there"),
        (["wassup"], "Wassup"), (["whatsup"], "What's up"), (["sup"], "Sup"), (["yo"], "Yo"), (["hey"], "Hey"),
        (["hi"], "Hi"), (["hello"], "Hello"), (["hiya"], "Hiya"), (["howdy"], "Howdy"), (["heya"], "Heya"),
        (["greetings"], "Greetings"), (["morning"], "Morning"), (["evening"], "Evening"),
    ]
    /// Said before "Beacon" but not greetings themselves: "Okay Beacon" gets the default reply.
    private static let prefixes: Set<String> = ["ok", "okay"]
    /// "Beacon" plus the spellings Whisper tends to produce for it.
    private static let names: Set<String> = ["beacon", "beacons", "beekon", "beakon", "beckon"]
    /// Greetings that can lead a request without "Beacon" ("Hey, open Mail"), when a comma follows them.
    private static let requestOpeners: Set<String> = ["Hey", "Hi", "Hello", "Yo", "Hey there", "Hi there", "Hello there"]

    /// What Beacon says to a lone greeting: the user's own greeting back ("What's up, how can I help you?").
    static func reply(to text: String) -> String {
        "\(parse(text)?.greeting ?? "Hey"), how can I help you?"
    }

    /// The default reply, for "Beacon" on its own.
    static let greeting = "Hey, how can I help you?"

    /// Matches a lone greeting, or "[greeting] Beacon [request]". Returns nil when the text is something else.
    static func match(_ text: String) -> WakePhrase? {
        guard let parsed = parse(text) else { return nil }
        if parsed.rest.isEmpty { return .only }
        if parsed.named { return .followedBy(parsed.rest) }
        // "Hey, open Mail": a plain greeting, then a comma, then the request.
        if let greeting = parsed.greeting, requestOpeners.contains(greeting), parsed.commaAfterGreeting {
            return .followedBy(parsed.rest)
        }
        return nil
    }

    private struct Parsed {
        var greeting: String?
        var named: Bool
        var rest: String
        var commaAfterGreeting: Bool
    }

    private static func parse(_ text: String) -> Parsed? {
        let words = wordRanges(in: text)
        guard !words.isEmpty else { return nil }
        let keys = words.map { text[$0].lowercased().replacingOccurrences(of: "'", with: "").replacingOccurrences(of: "’", with: "") }
        var index = 0
        var greeting: String?
        if let phrase = greetingPhrases.first(where: { keys.starts(with: $0.words) }) {
            greeting = phrase.reply
            index = phrase.words.count
        } else if prefixes.contains(keys[0]), keys.count > 1, names.contains(keys[1]) {
            index = 1
        }
        let named = index < keys.count && names.contains(keys[index])
        if named { index += 1 }
        guard greeting != nil || named else { return nil }
        let end = index > 0 ? words[index - 1].upperBound : text.startIndex
        let tail = text[end...]
        let comma = tail.prefix(while: { !$0.isLetter && !$0.isNumber }).contains(",")
        let rest = tail.drop(while: { !$0.isLetter && !$0.isNumber }).trimmingCharacters(in: .whitespacesAndNewlines)
        return Parsed(greeting: greeting, named: named, rest: rest, commaAfterGreeting: comma)
    }

    private static func wordRanges(in text: String) -> [Range<String.Index>] {
        var ranges: [Range<String.Index>] = []
        var start: String.Index?
        var i = text.startIndex
        while i < text.endIndex {
            let c = text[i]
            let isWord = c.isLetter || c.isNumber || c == "'" || c == "’"
            if isWord, start == nil { start = i }
            if !isWord, let s = start { ranges.append(s..<i); start = nil }
            i = text.index(after: i)
        }
        if let s = start { ranges.append(s..<text.endIndex) }
        return ranges
    }
}
