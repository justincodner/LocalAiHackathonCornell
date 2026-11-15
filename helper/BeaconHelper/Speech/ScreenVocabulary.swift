// Owner: Mac owner (H4). Turns the words visible on screen into a short Whisper decoding prompt, so names
// shown in the frontmost window ("Kingsnipeshot", "Krillion") are transcribed the way they are spelled.
// Pure logic only: the accessibility read lives in AccessibilityService.screenText, the token encoding in
// WhisperTranscriber.
import Foundation

/// Visible text of the frontmost window, read when push-to-talk starts.
struct ScreenText: Sendable, Equatable {
    var app: String
    var window: String
    var labels: [String]
}

enum ScreenVocabulary {
    /// Whisper's prompt window is about 224 tokens and WhisperKit keeps at most 111 of them; long prompts
    /// raise the risk of the model echoing them back, so stay well under.
    static let maxTokens = 100
    static let maxWords = 40
    static let maxCharacters = 240

    /// Builds the hint: distinctive words first (capitalised or camelCase names not in the common-word list),
    /// then other uncommon lowercase words, each group in on-screen order, case-insensitively deduplicated,
    /// joined with ", " and capped by word count and characters. Empty when nothing distinctive is visible.
    static func hint(for screen: ScreenText, maxWords: Int = maxWords, maxCharacters: Int = maxCharacters) -> String {
        hint(sources: [screen.app, screen.window] + screen.labels, maxWords: maxWords, maxCharacters: maxCharacters)
    }

    static func hint(sources: [String], maxWords: Int = maxWords, maxCharacters: Int = maxCharacters) -> String {
        struct Entry { var form: String; var tier: Int; var order: Int }
        var entries: [String: Entry] = [:]
        var order = 0
        for source in sources {
            for word in words(in: stripNoise(source)) {
                guard let tier = tier(of: word) else { continue }
                let key = word.lowercased()
                if var existing = entries[key] {
                    // Prefer the capitalised spelling and the better tier, but keep the first position.
                    if tier < existing.tier { existing.tier = tier; existing.form = word }
                    entries[key] = existing
                } else {
                    entries[key] = Entry(form: word, tier: tier, order: order)
                    order += 1
                }
            }
        }
        let ranked = entries.values.sorted { ($0.tier, $0.order) < ($1.tier, $1.order) }
        var out: [String] = []
        var length = 0
        for entry in ranked {
            guard out.count < maxWords else { break }
            let added = entry.form.count + (out.isEmpty ? 0 : 2)
            if length + added > maxCharacters { continue }
            out.append(entry.form)
            length += added
        }
        return out.joined(separator: ", ")
    }

    // MARK: Noise and words

    /// Removes browser tab noise ("- Memory usage - 383 MB"), unread counts ("(8623)"), URLs and e-mail-like tokens.
    static func stripNoise(_ text: String) -> String {
        var s = Labels.withoutTabNoise(text)
        let patterns = [
            #"(?i)\b(?:high\s+)?memory usage\s*-?\s*[0-9.,]+\s*[KMGT]B\b"#,
            #"\(\s*[0-9.,]+\+?\s*\)"#,            // (8623), (12+)
            #"(?i)\b(?:https?://|www\.)\S+"#,     // URLs
            #"\S+@\S+\.\S+"#,                     // e-mail addresses
        ]
        for p in patterns {
            s = s.replacingOccurrences(of: p, with: " ", options: .regularExpression)
        }
        return s
    }

    /// Splits text into words: letters and digits, keeping inner apostrophes, hyphens and dots ("Wi-Fi", "Node.js").
    /// A possessive "'s" is dropped.
    static func words(in text: String) -> [String] {
        var out: [String] = []
        let separators = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "'’-.")).inverted
        for raw in text.components(separatedBy: separators) {
            var w = raw.trimmingCharacters(in: CharacterSet(charactersIn: "'’-."))
            if w.hasSuffix("'s") || w.hasSuffix("’s") { w = String(w.dropLast(2)) }
            if !w.isEmpty { out.append(w) }
        }
        return out
    }

    /// 0 = capitalised or mixed-case uncommon word (names), 1 = lowercase uncommon word, nil = drop.
    static func tier(of word: String) -> Int? {
        guard word.count >= 3, word.count <= 30 else { return nil }
        let letters = word.filter(\.isLetter).count
        let digits = word.filter(\.isNumber).count
        guard letters >= 2, digits <= 2, Double(letters) / Double(word.count) >= 0.6 else { return nil }
        if commonWords.contains(word.lowercased()) { return nil }
        return word.contains(where: \.isUppercase) ? 0 : (word.count >= 5 ? 1 : nil)
    }

    // MARK: Fitting the token budget

    /// Drops trailing hint words until `encode` yields at most `maxTokens` tokens. `encode` is the tokenizer.
    static func fit(_ hint: String, maxTokens: Int = maxTokens, encode: (String) -> [Int]) -> [Int] {
        var parts = hint.components(separatedBy: ", ").filter { !$0.isEmpty }
        while !parts.isEmpty {
            let tokens = encode(" " + parts.joined(separator: ", "))
            if tokens.count <= maxTokens { return tokens }
            parts.removeLast()
        }
        return []
    }

    // MARK: Prompt echo

    /// True when the transcript looks like Whisper regurgitated the prompt instead of hearing speech:
    /// at least three words and three quarters or more of them are hint words.
    static func looksLikeEcho(transcript: String, hint: String) -> Bool {
        let hintWords = Set(words(in: hint).map { $0.lowercased() })
        guard !hintWords.isEmpty else { return false }
        let spoken = words(in: transcript).map { $0.lowercased() }
        guard spoken.count >= 3 else { return false }
        let hits = spoken.filter { hintWords.contains($0) }.count
        return Double(hits) / Double(spoken.count) >= 0.75
    }

    // MARK: Common words

    /// Everyday English and UI words Whisper already spells right; they would only dilute the prompt.
    static let commonWords: Set<String> = Set("""
    the and for are but not you all any can had her was one our out day get has him his how man new now old see two \
    way who boy did its let put say she too use that with have this will your from they know want been good much some \
    time very when come here just like long make many more only over such take than them well were what where which \
    while about after again also back because before being below between both could down each even every first going \
    great into most must never other people really right same should still their there these thing think those three \
    through under until upon used using very would year years yes yet able above across already always another around \
    away best better big came cannot close done else end enough ever far few find give gone got hand high however keep \
    kind last least left less life little look looking made may maybe might mine near need next nothing off often once \
    open own part place please quite rather said seem seen several shall show side since small something soon start \
    sure tell than thanks thank then today together tomorrow told took toward try turn upon want week why within without \
    work world yesterday your yours

    button buttons menu menus item items window windows file files edit view help tab tabs page pages new close open \
    save share search settings setting preferences options option tools tool format insert delete remove add cancel \
    done back forward next previous home reload refresh stop copy paste cut undo redo select selected all none more less \
    show hide toggle sidebar toolbar bookmarks bookmark history downloads download upload extensions extension profile \
    account sign log login logout inbox sent drafts draft archive spam trash junk folder folders message messages mail \
    email reply forward compose send attach attachment subject unread read flagged flag notifications notification \
    chat chats call calls video audio voice mute unmute camera microphone screen full zoom minimize maximize group \
    groups channel channels direct members member online offline status available away busy invite friends friend \
    contacts contact people person name names title text image images photo photos link links address bar address \
    field list table row column cell sort filter filters date time today yesterday new untitled document documents \
    note notes calendar event events reminder reminders general recent recents favorites favourite favorites library \
    music movies apps app application applications system finder desktop recents shared private public default custom \
    enter return escape space tab memory usage loading load error warning info information details summary overview \
    privacy security terms policy about contact support feedback report learn learn more get started sign in sign up \
    continue skip got accept decline allow deny ok yes no on off up down left right top bottom build version update
    """.components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: "\\")))
        .filter { !$0.isEmpty })
}
