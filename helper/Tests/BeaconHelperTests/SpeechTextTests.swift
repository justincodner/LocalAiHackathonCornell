import XCTest
@testable import BeaconHelper

final class SpeechTextTests: XCTestCase {
    func testSplitsSimpleSentences() {
        XCTAssertEqual(SentenceSplitter.split("Mail, inbox. Newest is from Sarah. Eleven more messages."),
                       ["Mail, inbox.", "Newest is from Sarah.", "Eleven more messages."])
    }

    func testKeepsAbbreviationsDecimalsAndInitials() {
        XCTAssertEqual(SentenceSplitter.split("Dr. Smith paid $3.50 today. J. R. Tolkien wrote it."),
                       ["Dr. Smith paid $3.50 today.", "J. R. Tolkien wrote it."])
        XCTAssertEqual(SentenceSplitter.split("Meet at 5 p.m. tomorrow, e.g. in the lobby."),
                       ["Meet at 5 p.m. tomorrow, e.g. in the lobby."])
    }

    func testQuestionExclamationQuotesAndNewlines() {
        XCTAssertEqual(SentenceSplitter.split("Send it?! \"Yes.\" Done\nNext line"),
                       ["Send it?!", "\"Yes.\"", "Done", "Next line"])
    }

    func testDropsEmptyAndPunctuationOnly() {
        XCTAssertEqual(SentenceSplitter.split(""), [])
        XCTAssertEqual(SentenceSplitter.split("  ...  \n\n !! "), [])
        XCTAssertEqual(SentenceSplitter.split("No terminator"), ["No terminator"])
    }

    func testSplitsVeryLongSentencesAtClauses() {
        let clause = "this clause is long enough to need its own piece of speech"
        let long = Array(repeating: clause, count: 6).joined(separator: ", ") + "."
        let parts = SentenceSplitter.split(long)
        XCTAssertGreaterThan(parts.count, 1)
        XCTAssertTrue(parts.allSatisfy { $0.count <= SentenceSplitter.maxSentenceLength })
        XCTAssertEqual(parts.joined(separator: " "), long)
    }

    func testTranscriptCleanerStripsWhisperArtefacts() {
        XCTAssertEqual(TranscriptCleaner.clean("[BLANK_AUDIO]"), "")
        XCTAssertEqual(TranscriptCleaner.clean(" [ Silence ] "), "")
        XCTAssertEqual(TranscriptCleaner.clean("<|startoftranscript|><|en|> Open Safari.<|endoftext|>"), "Open Safari.")
        XCTAssertEqual(TranscriptCleaner.clean("(music) Read my  mail [BLANK_AUDIO]"), "Read my mail")
        XCTAssertEqual(TranscriptCleaner.clean(" you"), "")
        XCTAssertEqual(TranscriptCleaner.clean("..."), "")
        XCTAssertEqual(TranscriptCleaner.clean("Thank you."), "Thank you.")
    }

    func testRateClamping() {
        XCTAssertEqual(SpeechRate.clamp(nil), 1.0)
        XCTAssertEqual(SpeechRate.clamp(.nan), 1.0)
        XCTAssertEqual(SpeechRate.clamp(.infinity), 1.0)
        XCTAssertEqual(SpeechRate.clamp(0.1), 0.5)
        XCTAssertEqual(SpeechRate.clamp(3), 1.5)
        XCTAssertEqual(SpeechRate.clamp(0.8), 0.8)
    }

    func testSystemRateMapping() {
        let normal = SystemSpeechEngine.utteranceRate(for: 1.0)
        XCTAssertLessThan(SystemSpeechEngine.utteranceRate(for: 0.5), normal)
        XCTAssertGreaterThan(SystemSpeechEngine.utteranceRate(for: 1.5), normal)
        XCTAssertEqual(SystemSpeechEngine.utteranceRate(for: 9), SystemSpeechEngine.utteranceRate(for: 1.5))
    }

    func testSequentialIDs() {
        let ids = SequentialID(prefix: "u")
        XCTAssertEqual(ids.next(), "u_0001")
        XCTAssertEqual(ids.next(), "u_0002")
    }

    func testWhisperModelNameResolution() throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("beacon-stt-\(UUID().uuidString)")
        let folder = base.appendingPathComponent("openai_whisper-tiny.en")
        for c in ["AudioEncoder", "TextDecoder", "MelSpectrogram"] {
            try FileManager.default.createDirectory(at: folder.appendingPathComponent("\(c).mlmodelc"),
                                                    withIntermediateDirectories: true)
        }
        defer { try? FileManager.default.removeItem(at: base) }
        XCTAssertEqual(WhisperModelLocator.resolve(name: "tiny.en", in: base)?.lastPathComponent, "openai_whisper-tiny.en")
        XCTAssertEqual(WhisperModelLocator.resolve(name: "openai_whisper-tiny.en", in: base)?.lastPathComponent,
                       "openai_whisper-tiny.en")
        XCTAssertNil(WhisperModelLocator.resolve(name: "large-v3-v20240930_626MB", in: base))
        XCTAssertNil(WhisperModelLocator.tokenizerFolder(for: folder))
    }

    func testMissingModelCancelsWithReason() async {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("beacon-none-\(UUID().uuidString)")
        let service = SpeechInputService(events: EventHub(), modelName: "tiny.en", modelsDir: base)
        let tooShort = await service.process(samples: [Float](repeating: 0, count: 1600))
        XCTAssertEqual(tooShort, .cancelled(reason: "too_short"))
        let missing = await service.process(samples: [Float](repeating: 0, count: 16_000))
        XCTAssertEqual(missing, .cancelled(reason: "stt_model_missing"))
    }
}
