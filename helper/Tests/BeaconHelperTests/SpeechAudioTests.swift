import AVFoundation
import XCTest
@testable import BeaconHelper

final class SpeechAudioTests: XCTestCase {
    private func sine(seconds: Double, rate: Double, freq: Double = 440, amplitude: Float = 0.95) -> [Float] {
        (0..<Int(seconds * rate)).map { amplitude * Float(sin(2 * .pi * freq * Double($0) / rate)) }
    }

    // MARK: Assembly

    func testAssembleJoinsClipsWithPausesAndFadesEdges() {
        let rate = 1000.0
        let a = [Float](repeating: 0.5, count: 100), b = [Float](repeating: -0.5, count: 50)
        let out = SpeechAudio.assemble([a, [], b], sampleRate: rate, pause: 0.15, fade: 0.01)
        XCTAssertEqual(out.count, 100 + 150 + 50, "empty clips are skipped; one 150-sample pause between the two")
        XCTAssertEqual(out[0], 0, "fade-in starts from silence")
        XCTAssertEqual(out[99], 0, "fade-out ends in silence")
        XCTAssertEqual(out[50], 0.5, "the middle is untouched")
        XCTAssertLessThan(out[5], out[9], "fade-in rises")
        XCTAssertTrue(out[100..<250].allSatisfy { $0 == 0 }, "the pause is silent")
        XCTAssertEqual(out[250], 0)
        XCTAssertEqual(out[275], -0.5)
        XCTAssertEqual(out.last, 0)
    }

    func testAssembleOfNothingIsEmptyAndShortClipsSurvive() {
        XCTAssertTrue(SpeechAudio.assemble([]).isEmpty)
        XCTAssertTrue(SpeechAudio.assemble([[], []]).isEmpty)
        XCTAssertEqual(SpeechAudio.assemble([[0.3]], sampleRate: 1000, fade: 0.01), [0.3], "too short to fade")
    }

    func testAssembleTrimsTrailingSilenceSoSentenceGapsStayShort() {
        let rate = 1000.0
        // 100 samples of speech, then 500 ms of TTSKit-style tail silence (with a little noise floor).
        let tail = (0..<500).map { Float($0 % 2 == 0 ? 0.001 : -0.001) }
        let sentence = [Float](repeating: 0.4, count: 100) + tail
        let out = SpeechAudio.assemble([sentence, sentence], sampleRate: rate, pause: 0.15, fade: 0.005)
        // Each clip keeps 30 ms of its tail: 130 + 150 + 130.
        XCTAssertEqual(out.count, 130 + 150 + 130)
        XCTAssertTrue(SpeechAudio.trimmedSilence([0, 0.001, -0.002], sampleRate: rate).isEmpty, "silence-only clips vanish")
    }

    func testSoftLimiterKeepsQuietAudioAndTamesPeaks() {
        let quiet: [Float] = [0, 0.2, -0.5, 0.79]
        XCTAssertEqual(SpeechAudio.softLimited(quiet), quiet, "audio under the knee is untouched")
        let loud = SpeechAudio.softLimited([1.4, -2.0, 0.9, 1.0])
        XCTAssertTrue(loud.allSatisfy { abs($0) < 1 }, "nothing reaches full scale: \(loud)")
        XCTAssertGreaterThan(loud[0], 0.9)
        XCTAssertLessThan(loud[1], -0.9)
        XCTAssertGreaterThan(loud[3], loud[2], "louder input stays louder")
    }

    // MARK: Format conversion

    func testResampleSineToPlayerRateKeepsLengthAndLevel() {
        for source in [22_050.0, 24_000, 48_000] {
            let input = sine(seconds: 1, rate: source)
            let out = SpeechAudio.resample(input, from: source, to: SpeechAudio.sampleRate)
            let expected = Int(SpeechAudio.sampleRate)
            XCTAssertLessThanOrEqual(abs(out.count - expected), 2, "\(source) Hz: \(out.count) samples, want \(expected)")
            let peak = out.map(abs).max() ?? 0
            XCTAssertLessThanOrEqual(SpeechAudio.softLimited(out).map(abs).max() ?? 2, 1.0, "\(source) Hz")
            XCTAssertLessThanOrEqual(peak, 1.0, "\(source) Hz peak \(peak)")
            XCTAssertGreaterThan(peak, 0.85, "\(source) Hz keeps its level (peak \(peak))")
            // Still a 440 Hz tone: about 880 zero crossings in one second.
            let crossings = zip(out, out.dropFirst()).filter { ($0 < 0) != ($1 < 0) }.count
            XCTAssertEqual(Double(crossings), 880, accuracy: 6, "\(source) Hz")
        }
    }

    func testResampleTo24kHalvesA48kClip() {
        let out = SpeechAudio.resample(sine(seconds: 0.5, rate: 48_000), from: 48_000, to: 24_000)
        XCTAssertLessThanOrEqual(abs(out.count - 12_000), 2)
    }

    func testMonoFloatReadsInt16AndStereoBuffers() throws {
        let fmt = try XCTUnwrap(AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 22_050, channels: 2, interleaved: true))
        let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: 4))
        buffer.frameLength = 4
        let data = buffer.int16ChannelData![0]
        let pairs: [(Int16, Int16)] = [(16_384, 16_384), (-32_768, -32_768), (16_384, 0), (0, 0)]
        for (i, p) in pairs.enumerated() { data[2 * i] = p.0; data[2 * i + 1] = p.1 }
        XCTAssertEqual(SpeechAudio.monoFloat(buffer), [0.5, -1, 0.25, 0])
    }

    // MARK: Engine choice

    func testChoosesTTSKitOnlyWhenItCanRenderWithinBudget() {
        let ttskit = SpeechAudio.Candidate(speedFactor: 0.64, rendersRate: false)
        let compact = SpeechAudio.Candidate(speedFactor: 20, rendersRate: true)
        // An 8-character phrase is ~0.6 s of audio: ~1.0 s to render at 0.64x, inside the 1.5 s budget.
        let short = SpeechAudio.estimatedRenderSeconds(characters: 8, sentences: 1, rate: 1, candidate: ttskit)
        XCTAssertLessThanOrEqual(short, SpeechAudio.renderBudget)
        XCTAssertEqual(SpeechAudio.chooseEngine([ttskit, compact], characters: 8, sentences: 1, rate: 1), 0)
        // A two-sentence reply would take ~8 s at 0.64x: the system voice instead.
        XCTAssertEqual(SpeechAudio.chooseEngine([ttskit, compact], characters: 70, sentences: 2, rate: 1), 1)
        // A fast TTSKit (idle machine, 3x) takes the same reply.
        let fast = SpeechAudio.Candidate(speedFactor: 3, rendersRate: false)
        XCTAssertEqual(SpeechAudio.chooseEngine([fast, compact], characters: 50, sentences: 2, rate: 1), 0)
    }

    func testUnreadyEnginesAreSkippedAndTheLastIsAlwaysAvailable() {
        let notLoaded = SpeechAudio.Candidate(speedFactor: 0, rendersRate: false)
        let natural = SpeechAudio.Candidate(speedFactor: 2, rendersRate: true)
        let compact = SpeechAudio.Candidate(speedFactor: 0, rendersRate: true)
        XCTAssertEqual(SpeechAudio.chooseEngine([notLoaded, natural, compact], characters: 20, sentences: 1, rate: 1), 1)
        XCTAssertEqual(SpeechAudio.chooseEngine([notLoaded, natural, compact], characters: 500, sentences: 6, rate: 1), 2,
                       "too long for anything measured: the last (compact) voice, whatever its estimate")
        XCTAssertEqual(SpeechAudio.chooseEngine([compact], characters: 10, sentences: 1, rate: 1), 0)
        XCTAssertEqual(SpeechAudio.chooseEngine([], characters: 10, sentences: 1, rate: 1), 0)
    }

    func testFasterRateMeansLessAudioOnlyForEnginesThatRenderTheRate() {
        let system = SpeechAudio.Candidate(speedFactor: 2, rendersRate: true)
        let ttskit = SpeechAudio.Candidate(speedFactor: 2, rendersRate: false)
        let systemFast = SpeechAudio.estimatedRenderSeconds(characters: 60, sentences: 1, rate: 1.5, candidate: system)
        let systemNormal = SpeechAudio.estimatedRenderSeconds(characters: 60, sentences: 1, rate: 1, candidate: system)
        XCTAssertLessThan(systemFast, systemNormal)
        XCTAssertEqual(SpeechAudio.estimatedRenderSeconds(characters: 60, sentences: 1, rate: 1.5, candidate: ttskit),
                       SpeechAudio.estimatedRenderSeconds(characters: 60, sentences: 1, rate: 1, candidate: ttskit))
    }
}
