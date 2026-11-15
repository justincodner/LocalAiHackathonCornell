import AVFoundation
import XCTest
import WhisperKit
@testable import BeaconHelper

/// Real-model measurements. Opt in with `BEACON_SPEECH_BENCH=1 swift test --filter SpeechBenchTests`
/// (needs the models under models/, or BEACON_MODELS_DIR=/abs/path/models). The TTS benches render to WAV files in
/// BEACON_BENCH_OUT (default: a temporary folder) and never play through the speakers.
final class SpeechBenchTests: XCTestCase {
    private var enabled: Bool { ProcessInfo.processInfo.environment["BEACON_SPEECH_BENCH"] == "1" }

    /// Physical footprint (what Activity Monitor shows as Memory), in MB.
    private static func residentMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
    }

    private func makeClip(_ sentence: String = "Open Safari and read me the newest email from Sarah.") throws -> [Float] {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("beacon-bench-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let aiff = dir.appendingPathComponent("clip.aiff").path
        let wav = dir.appendingPathComponent("clip.wav").path
        func run(_ tool: String, _ args: [String]) throws {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: tool)
            p.arguments = args
            try p.run()
            p.waitUntilExit()
        }
        try run("/usr/bin/say", ["-o", aiff, sentence])
        try run("/usr/bin/afconvert", ["-f", "WAVE", "-d", "LEF32@16000", "-c", "1", aiff, wav])
        return try AudioProcessor.loadAudioAsFloatArray(fromPath: wav)
    }

    func testWhisperModels() async throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let samples = try makeClip()
        let audioMS = Int(Double(samples.count) / 16)
        let models = ProcessInfo.processInfo.environment["BEACON_BENCH_MODELS"]?.split(separator: ",").map(String.init)
            ?? ["tiny.en", "large-v3-v20240930_626MB"]
        for model in models {
            let before = Self.residentMB()
            let service = SpeechInputService(events: EventHub(), modelName: model)
            let loadStart = CFAbsoluteTimeGetCurrent()
            await service.prewarm()
            let loadMS = Int((CFAbsoluteTimeGetCurrent() - loadStart) * 1000)
            let after = Self.residentMB()
            var runs: [Int] = []
            var text = ""
            for _ in 0..<3 {
                let outcome = await service.process(samples: samples)
                guard case let .final(t, _, sttMS) = outcome else { return XCTFail("\(model): \(outcome)") }
                runs.append(sttMS)
                text = t
            }
            print("BENCH stt model=\(model) audio_ms=\(audioMS) load_ms=\(loadMS) stt_ms=\(runs) rss_delta_mb=\(Int(after - before)) text=\"\(text)\"")
            XCTAssertTrue(text.lowercased().contains("safari"))
        }
    }

    /// Screen-hint prompt: the same clip with and without on-screen vocabulary, plus a silent clip
    /// that must not come back as the prompt echoed.
    func testScreenHintPrompt() async throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let model = ProcessInfo.processInfo.environment["BEACON_BENCH_MODELS"]?.split(separator: ",").first.map(String.init)
            ?? "tiny.en"
        let service = SpeechInputService(events: EventHub(), modelName: model)
        await service.prewarm()
        let screen = ScreenText(app: "Discord", window: "Krillion - Discord",
                                labels: ["Kingsnipeshot", "Krillion", "Direct Messages", "Search", "Settings"])
        let hint = ScreenVocabulary.hint(for: screen)
        for sentence in ["Send a message to Krillion saying hello.", "Open the chat with Kingsnipeshot."] {
            let samples = try makeClip(sentence)
            let plain = await service.process(samples: samples)
            let hinted = await service.process(samples: samples, hint: hint)
            print("BENCH hint model=\(model) hint=\"\(hint)\" said=\"\(sentence)\" plain=\(plain) hinted=\(hinted)")
            guard case .final = hinted else { return XCTFail("hinted transcription failed: \(hinted)") }
        }
        let silence = [Float](repeating: 0, count: 32_000)
        let silent = await service.process(samples: silence, hint: hint)
        print("BENCH hint silence=\(silent)")
        if case let .final(text, _, _) = silent {
            XCTAssertFalse(ScreenVocabulary.looksLikeEcho(transcript: text, hint: hint), text)
        }
    }

    private static let sample = "Newest is from Sarah Chen about lunch tomorrow. Eleven more messages."

    private var outDir: URL {
        let raw = ProcessInfo.processInfo.environment["BEACON_BENCH_OUT"]
        let dir = raw.map { URL(fileURLWithPath: $0) } ?? FileManager.default.temporaryDirectory.appendingPathComponent("beacon-bench")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Runs `op` while pumping the main run loop (AVSpeechSynthesizer delivers its buffers there).
    private func pumping<T>(timeout: Double = 120, _ op: @escaping @Sendable () async throws -> T) throws -> T {
        let box = LockedValue<Result<T, Error>?>(nil)
        Task.detached { do { box.set(.success(try await op())) } catch { box.set(.failure(error)) } }
        let deadline = Date().addingTimeInterval(timeout)
        while box.get() == nil, Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.005)) }
        guard let result = box.get() else { throw HelperError(.actionFailed, "timed out") }
        return try result.get()
    }

    /// Renders `text` with `engine` exactly as the queue does (assembled, limited) and writes a 48 kHz WAV.
    private func renderToWAV(_ engine: SpeechEngine, _ text: String, file: String) throws -> (render: Double, audio: Double, peak: Float) {
        let t0 = CFAbsoluteTimeGetCurrent()
        let clips = try pumping { try await engine.render(SentenceSplitter.split(text), rate: 1, token: SpeechCancelToken()) }
        let render = CFAbsoluteTimeGetCurrent() - t0
        let audio = SpeechAudio.softLimited(SpeechAudio.assemble(clips))
        let url = outDir.appendingPathComponent(file)
        let wav = try AVAudioFile(forWriting: url, settings: ClipPlayer.format.settings)
        let buffer = AVAudioPCMBuffer(pcmFormat: ClipPlayer.format, frameCapacity: AVAudioFrameCount(audio.count))!
        buffer.frameLength = AVAudioFrameCount(audio.count)
        audio.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: audio.count) }
        try wav.write(from: buffer)
        let seconds = Double(audio.count) / SpeechAudio.sampleRate
        print(String(format: "BENCH render engine=%@ file=%@ render_s=%.2f audio_s=%.2f speed=%.2fx peak=%.3f",
                     engine.name, url.path, render, seconds, seconds / max(render, 0.001), audio.map(abs).max() ?? 0))
        return (render, seconds, audio.map(abs).max() ?? 0)
    }

    func testTTSKitRender() throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let before = Self.residentMB()
        let engine = TTSKitEngine()
        let start = CFAbsoluteTimeGetCurrent()
        try pumping(timeout: 600) { try await engine.prepare() }
        print("BENCH tts load_ms=\(Int(engine.loadSeconds * 1000)) prepare_total_ms=\(Int((CFAbsoluteTimeGetCurrent() - start) * 1000)) "
              + String(format: "warmup_speed=%.2fx", engine.warmupSpeedFactor) + " rss_delta_mb=\(Int(Self.residentMB() - before))")
        for (i, text) in ["Mail, inbox.", Self.sample].enumerated() {
            let r = try renderToWAV(engine, text, file: "ttskit-\(i).wav")
            XCTAssertGreaterThan(r.audio, 0.3)
            XCTAssertLessThanOrEqual(r.peak, 1.0)
        }
    }

    /// Long replies must render to the end: the synthesizer sends an empty buffer mid-utterance (around 10.9 s
    /// here), which used to be taken as the end and cut the reply short. Runs without BEACON_SPEECH_BENCH (silent).
    func testSystemVoiceRendersALongReplyToTheEnd() throws {
        let engine = SystemSpeechEngine(voice: SystemSpeechEngine.compactEnglishVoice(), assumedSpeed: 0)
        let text = "The game being played appears to be FreeBetRange based on the text visible on the screen. "
            + "It's a betting or gambling application with various buttons for actions like folding, raising, and "
            + "calling. The specific game title isn't explicitly stated as FreeBetRange, but that is the main "
            + "interface element displayed."
        let clips = try pumping { try await engine.render(SentenceSplitter.split(text), rate: 1, token: SpeechCancelToken()) }
        let seconds = Double(clips.reduce(0) { $0 + $1.count }) / SpeechAudio.sampleRate
        XCTAssertGreaterThan(seconds, 14, "the whole reply is rendered, not just its first ~11 s")
    }

    func testSystemVoiceRender() throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        print("BENCH en-US voices: " + AVSpeechSynthesisVoice.speechVoices().filter { $0.language == "en-US" }.map { "\($0.identifier)/\($0.quality.rawValue)" }.joined(separator: ", "))
        var voices: [(String, AVSpeechSynthesisVoice?)] = [("compact", SystemSpeechEngine.compactEnglishVoice())]
        if let natural = SystemSpeechEngine.naturalEnglishVoice() { voices.append(("natural", natural)) }
        for (label, voice) in voices {
            let engine = SystemSpeechEngine(voice: voice, assumedSpeed: 0)
            try pumping { try await engine.prepare() }
            for i in 0..<2 {
                let r = try renderToWAV(engine, Self.sample, file: "system-\(label)-\(i).wav")
                XCTAssertGreaterThan(r.audio, 2)
                XCTAssertLessThanOrEqual(r.peak, 1.0)
            }
            print(String(format: "BENCH system voice=%@ (%@) measured_speed=%.2fx", voice?.identifier ?? "-", label, engine.speedFactor))
        }
    }

    /// Barge-in while the system voice is still rendering: tts.finished must follow within 100 ms. Silent (fake output).
    func testStopDuringSystemRender() throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let engine = SystemSpeechEngine(voice: SystemSpeechEngine.naturalEnglishVoice() ?? SystemSpeechEngine.compactEnglishVoice(),
                                        assumedSpeed: 0)
        let rec = EventRecorder()
        let queue = SpeechQueue(engines: { _ in [engine] }, player: FakeAudioOutput(), emit: { rec.record($0, $1) })
        let id = queue.enqueue(text: String(repeating: Self.sample + " ", count: 4), interrupt: false, rate: nil)
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        let s0 = CFAbsoluteTimeGetCurrent()
        queue.stop()
        while rec.finished(id) == nil, CFAbsoluteTimeGetCurrent() - s0 < 5 { RunLoop.main.run(until: Date().addingTimeInterval(0.002)) }
        let stopMS = Int((CFAbsoluteTimeGetCurrent() - s0) * 1000)
        print("BENCH system stop_during_render_ms=\(stopMS) started=\(rec.started(id))")
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, true)
        XCTAssertLessThan(stopMS, 100)
    }
}
