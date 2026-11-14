// Owner: Mac owner (H4/H5). Pure text helpers for speech: sentence splitting, transcript cleanup, rate clamping, ids.
import Foundation

/// Splits text into sentences so speech can be synthesised and stopped sentence by sentence (spec 4.3).
enum SentenceSplitter {
    /// Abbreviations that end in a period but do not end a sentence.
    private static let abbreviations: Set<String> = [
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no", "approx",
        "inc", "ltd", "co", "corp", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "a.m", "p.m", "u.s",
    ]

    /// Sentences longer than this are further split at commas, semicolons or dashes so the first audio comes quickly.
    static let maxSentenceLength = 220

    static func split(_ text: String) -> [String] {
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        var sentences: [String] = []
        var current = ""
        let chars = Array(normalized)
        var i = 0
        while i < chars.count {
            let c = chars[i]
            if c == "\n" {
                // Blank lines and bullet lists are sentence breaks.
                flush(&current, into: &sentences)
                i += 1
                continue
            }
            current.append(c)
            if c == "." || c == "!" || c == "?" || c == "…" {
                // Absorb runs of terminators and closing quotes/brackets: "Really?!" or "done.)"
                var j = i + 1
                while j < chars.count, ".!?…\"'”’)]".contains(chars[j]) {
                    current.append(chars[j])
                    j += 1
                }
                let atEnd = j >= chars.count
                let followedBySpace = !atEnd && chars[j].isWhitespace
                if atEnd || (followedBySpace && isSentenceEnd(current, terminator: c, next: nextNonSpace(chars, from: j))) {
                    flush(&current, into: &sentences)
                }
                i = j
                continue
            }
            i += 1
        }
        flush(&current, into: &sentences)
        return sentences.flatMap(splitLong)
    }

    private static func nextNonSpace(_ chars: [Character], from index: Int) -> Character? {
        var k = index
        while k < chars.count, chars[k].isWhitespace { k += 1 }
        return k < chars.count ? chars[k] : nil
    }

    private static func isSentenceEnd(_ sentence: String, terminator: Character, next: Character?) -> Bool {
        guard terminator == "." else { return true }
        // Last word before the period, lowercased, without the trailing period.
        let trimmed = sentence.trimmingCharacters(in: CharacterSet(charactersIn: ".\"'”’)] "))
        let lastWord = trimmed.split(whereSeparator: { $0.isWhitespace }).last.map(String.init)?.lowercased() ?? ""
        if abbreviations.contains(lastWord) { return false }
        // Single initials ("J. Smith") do not end sentences.
        if lastWord.count == 1, lastWord.first?.isLetter == true, next?.isUppercase == true { return false }
        // Decimal numbers never reach here (no whitespace after the period), but "3. apples" lowercase continuation:
        if let next, next.isLowercase { return false }
        return true
    }

    private static func flush(_ current: inout String, into sentences: inout [String]) {
        let s = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.rangeOfCharacter(from: .alphanumerics) != nil {
            sentences.append(s)
        }
        current = ""
    }

    /// Splits an over-long sentence at clause punctuation, keeping pieces at least ~40 characters.
    private static func splitLong(_ sentence: String) -> [String] {
        guard sentence.count > maxSentenceLength else { return [sentence] }
        var parts: [String] = []
        var current = ""
        for c in sentence {
            current.append(c)
            if (c == "," || c == ";" || c == ":" || c == "—") && current.count >= 40 && current.count >= maxSentenceLength / 2 {
                parts.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            }
        }
        let tail = current.trimmingCharacters(in: .whitespaces)
        if !tail.isEmpty {
            if tail.count < 20, let last = parts.popLast() { parts.append(last + " " + tail) } else { parts.append(tail) }
        }
        return parts
    }
}

/// Cleans raw Whisper output: strips special tokens and non-speech artefacts such as `[BLANK_AUDIO]`.
enum TranscriptCleaner {
    private static let artefactPatterns: [String] = [
        #"<\|[^|>]*\|>"#,                 // <|startoftranscript|>, <|en|>, <|0.00|>
        #"\[[^\]]*\]"#,                   // [BLANK_AUDIO], [Music], [ Silence ]
        #"\((?:[^)]*(?:music|silence|blank|noise|inaudible|applause|laugh)[^)]*)\)"#, // (music), (silence)
        #"\*[^*]*\*"#,                    // *sighs*
        #"♪+"#,
    ]

    static func clean(_ raw: String) -> String {
        var text = raw
        for pattern in artefactPatterns {
            text = text.replacingOccurrences(of: pattern, with: " ", options: [.regularExpression, .caseInsensitive])
        }
        text = text.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Whisper's classic hallucinations on silence.
        let lowered = text.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".!? "))
        if ["you", "thank you", "thanks for watching", "thank you for watching", "bye"].contains(lowered) && raw.count < 40 {
            // Keep "thank you" only if the caller says the audio was clearly speech; we cannot know, so keep it.
            // Only drop the single word "you", which is almost always a silence artefact.
            if lowered == "you" { return "" }
        }
        // Nothing but punctuation means nothing was said.
        if text.rangeOfCharacter(from: .alphanumerics) == nil { return "" }
        return text
    }
}

enum SpeechRate {
    static let minimum = 0.5
    static let maximum = 1.5
    static let normal = 1.0

    /// Clamps a requested rate to 0.5...1.5; nil, NaN or infinite gives 1.0.
    static func clamp(_ rate: Double?) -> Double {
        guard let rate, rate.isFinite else { return normal }
        return min(max(rate, minimum), maximum)
    }
}

/// Thread-safe "u_0001" / "sp_0001" style id generator.
final class SequentialID: @unchecked Sendable {
    private let prefix: String
    private let lock = NSLock()
    private var counter = 0

    init(prefix: String) { self.prefix = prefix }

    func next() -> String {
        let n = lock.withLock { counter += 1; return counter }
        return String(format: "%@_%04d", prefix, n)
    }
}
