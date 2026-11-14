import XCTest
import WhisperKit
@testable import BeaconHelper

/// Real-model measurements. Opt in with `BEACON_SPEECH_BENCH=1 swift test --filter SpeechBenchTests`
/// (needs the models under models/; plays a few seconds of audio for the TTS part).
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

    private func makeClip() throws -> [Float] {
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
        try run("/usr/bin/say", ["-o", aiff, "Open Safari and read me the newest email from Sarah."])
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

    func testTTSKit() async throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let before = Self.residentMB()
        let engine = TTSKitEngine()
        let start = CFAbsoluteTimeGetCurrent()
        try await engine.prepare()
        let totalMS = Int((CFAbsoluteTimeGetCurrent() - start) * 1000)
        let after = Self.residentMB()
        print("BENCH tts load_ms=\(Int(engine.loadSeconds * 1000)) prepare_total_ms=\(totalMS) warmup_ttfa_ms=\(Int(engine.warmupFirstAudioSeconds * 1000)) rss_delta_mb=\(Int(after - before))")

        let rec = EventRecorder()
        let queue = SpeechQueue(engineProvider: { engine }, emit: { rec.record($0, $1) })
        var ttfas: [Int] = []
        for text in ["Mail, inbox.", "Newest is from Sarah Chen about lunch tomorrow. Eleven more messages."] {
            let t0 = CFAbsoluteTimeGetCurrent()
            let id = queue.enqueue(text: text, interrupt: false, rate: nil)
            rec.waitFor(timeout: 30) { rec.started(id) }
            ttfas.append(Int((CFAbsoluteTimeGetCurrent() - t0) * 1000))
            rec.waitFor(timeout: 30) { rec.finished(id) != nil }
        }
        // Barge-in: stop shortly after audio starts.
        let id = queue.enqueue(text: "This sentence is long and will be interrupted before it can finish speaking.", interrupt: false, rate: nil)
        rec.waitFor(timeout: 30) { rec.started(id) }
        usleep(300_000)
        let s0 = CFAbsoluteTimeGetCurrent()
        queue.stop()
        rec.waitFor(timeout: 5) { rec.finished(id) != nil }
        let stopMS = Int((CFAbsoluteTimeGetCurrent() - s0) * 1000)
        print("BENCH tts ttfa_ms=\(ttfas) stop_to_finished_ms=\(stopMS) rss_after_mb=\(Int(Self.residentMB()))")
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, true)
        XCTAssertLessThan(stopMS, 100)
    }

    func testSystemVoice() async throws {
        try XCTSkipUnless(enabled, "set BEACON_SPEECH_BENCH=1")
        let engine = SystemSpeechEngine()
        let rec = EventRecorder()
        let queue = SpeechQueue(engineProvider: { engine }, emit: { rec.record($0, $1) })
        let t0 = CFAbsoluteTimeGetCurrent()
        let id = queue.enqueue(text: "Mail, inbox. Eleven more messages.", interrupt: false, rate: 1.2)
        // AVSpeechSynthesizer delivers delegate callbacks on the main run loop.
        let deadline = Date().addingTimeInterval(15)
        while !rec.started(id), Date() < deadline { await Task.yield(); RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
        let ttfa = Int((CFAbsoluteTimeGetCurrent() - t0) * 1000)
        try await Task.sleep(nanoseconds: 400_000_000)
        let s0 = CFAbsoluteTimeGetCurrent()
        queue.stop()
        while rec.finished(id) == nil, Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.005)) }
        print("BENCH system ttfa_ms=\(ttfa) stop_ms=\(Int((CFAbsoluteTimeGetCurrent() - s0) * 1000))")
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, true)
    }
}
