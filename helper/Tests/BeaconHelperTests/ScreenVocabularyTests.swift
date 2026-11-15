import XCTest
@testable import BeaconHelper

final class ScreenVocabularyTests: XCTestCase {
    func testKeepsNamesAndDropsCommonWords() {
        let hint = ScreenVocabulary.hint(sources: ["Send message to Kingsnipeshot", "Search", "Settings", "Krillion"])
        XCTAssertEqual(hint, "Kingsnipeshot, Krillion")
    }

    func testDeduplicatesCaseInsensitivelyPreferringCapitalised() {
        let hint = ScreenVocabulary.hint(sources: ["krillion stream", "Krillion", "KRILLION", "Discord", "discord"])
        XCTAssertEqual(hint.components(separatedBy: ", ").filter { $0.lowercased() == "krillion" }.count, 1)
        XCTAssertTrue(hint.hasPrefix("Krillion, Discord"), hint)
    }

    func testStripsChromeTabNoiseCountsAndURLs() {
        let hint = ScreenVocabulary.hint(sources: [
            "Kingsnipeshot - Twitch - Memory usage - 383 MB",
            "Inbox (8623) - Gmail",
            "https://example.com/Zorblax?id=12345",
            "Contact jane.doe@example.com",
        ])
        let words = hint.components(separatedBy: ", ")
        XCTAssertEqual(words, ["Kingsnipeshot", "Twitch", "Gmail"])
        XCTAssertFalse(hint.contains("383"))
        XCTAssertFalse(hint.contains("8623"))
    }

    func testDropsNumbersAndIdentifiers() {
        let hint = ScreenVocabulary.hint(sources: ["12:45 PM", "2026", "a1b2c3d4e5", "Build 4521", "GPT4o", "Wi-Fi"])
        XCTAssertEqual(hint, "GPT4o, Wi-Fi")
    }

    func testOrdersCapitalisedNamesBeforeLowercaseRareWords() {
        let hint = ScreenVocabulary.hint(sources: ["the quokka sanctuary", "Thalberg", "with zymurgy notes", "Okonkwo"])
        XCTAssertEqual(hint, "Thalberg, Okonkwo, quokka, sanctuary, zymurgy")
    }

    func testPossessiveAndPunctuation() {
        XCTAssertEqual(ScreenVocabulary.hint(sources: ["Krillion's channel!", "@Kingsnipeshot", "#Valorant"]),
                       "Krillion, Kingsnipeshot, Valorant")
    }

    func testCapsWordCountAndCharacters() {
        let names = (0..<100).map { "Name\(String(UnicodeScalar(UInt8(65 + $0 % 26))))\(String(UnicodeScalar(UInt8(97 + $0 / 26))))x" }
        let byWords = ScreenVocabulary.hint(sources: names, maxWords: 10, maxCharacters: 1000)
        XCTAssertEqual(byWords.components(separatedBy: ", ").count, 10)
        XCTAssertEqual(byWords.components(separatedBy: ", ").first, names[0])
        let byChars = ScreenVocabulary.hint(sources: names, maxWords: 100, maxCharacters: 50)
        XCTAssertLessThanOrEqual(byChars.count, 50)
        let defaults = ScreenVocabulary.hint(sources: names)
        XCTAssertLessThanOrEqual(defaults.count, ScreenVocabulary.maxCharacters)
        XCTAssertLessThanOrEqual(defaults.components(separatedBy: ", ").count, ScreenVocabulary.maxWords)
    }

    func testEmptyScreenGivesEmptyHint() {
        XCTAssertEqual(ScreenVocabulary.hint(for: ScreenText(app: "Finder", window: "", labels: [])), "")
        XCTAssertEqual(ScreenVocabulary.hint(sources: ["OK", "Cancel", "Close window", "(12)"]), "")
    }

    func testScreenTextIncludesAppAndWindowFirst() {
        let screen = ScreenText(app: "Discord", window: "Krillion - Discord", labels: ["Kingsnipeshot"])
        XCTAssertEqual(ScreenVocabulary.hint(for: screen), "Discord, Krillion, Kingsnipeshot")
    }

    func testFitDropsTrailingWordsToTokenBudget() {
        // Fake tokenizer: one token per character.
        let encode: (String) -> [Int] = { Array(repeating: 1, count: $0.count) }
        let tokens = ScreenVocabulary.fit("Alpha, Bravo, Charlie, Delta", maxTokens: 14, encode: encode)
        XCTAssertEqual(tokens.count, " Alpha, Bravo".count)
        XCTAssertEqual(ScreenVocabulary.fit("", encode: encode), [])
        XCTAssertEqual(ScreenVocabulary.fit("Supercalifragilistic", maxTokens: 5, encode: encode), [])
    }

    func testEchoDetection() {
        let hint = "Kingsnipeshot, Krillion, Discord, Twitch"
        XCTAssertTrue(ScreenVocabulary.looksLikeEcho(transcript: "Kingsnipeshot, Krillion, Discord, Twitch.", hint: hint))
        XCTAssertTrue(ScreenVocabulary.looksLikeEcho(transcript: "Krillion Discord Twitch", hint: hint))
        XCTAssertFalse(ScreenVocabulary.looksLikeEcho(transcript: "Message Kingsnipeshot on Discord", hint: hint))
        XCTAssertFalse(ScreenVocabulary.looksLikeEcho(transcript: "Open Krillion", hint: hint))
        XCTAssertFalse(ScreenVocabulary.looksLikeEcho(transcript: "Krillion Discord Twitch", hint: ""))
    }

    func testAwaitHintTimesOutWithoutBlocking() async {
        let slow = Task<String, Never> {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            return "Late"
        }
        let start = Date()
        let hint = await SpeechInputService.awaitHint(slow, timeout: 0.05)
        XCTAssertNil(hint)
        XCTAssertLessThan(Date().timeIntervalSince(start), 1.0)
        slow.cancel()

        let ready = Task<String, Never> { "Krillion" }
        let readyHint = await SpeechInputService.awaitHint(ready, timeout: 1)
        XCTAssertEqual(readyHint, "Krillion")
        let empty = Task<String, Never> { "" }
        let emptyHint = await SpeechInputService.awaitHint(empty, timeout: 1)
        XCTAssertNil(emptyHint)
    }
}
