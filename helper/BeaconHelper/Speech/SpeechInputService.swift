// Owner: Mac owner (H4). Push-to-talk speech input: microphone capture plus WhisperKit transcription (spec 4.2, 4.6).
//
// Events: `listen.started` on start; on stop either `stt.final {utterance_id, text, audio_ms, stt_ms}` or
// `listen.cancelled {reason}`. Partials (`stt.partial`) are not emitted: utterances are 2 to 8 s and the
// whole buffer is transcribed on key release, which the spec allows.
import Foundation

final class SpeechInputService: SpeechInputProviding, @unchecked Sendable {
    /// Utterances shorter than this are dropped (spec 4.2).
    static let minimumAudioMS = 300

    let events: EventHub
    private let capture = AudioCapture()
    private let transcriber: WhisperTranscriber
    private let utteranceIDs = SequentialID(prefix: "u")
    private let lock = NSLock()
    private var listening = false
    private var modelMissing = false
    /// Bumped by every start/cancel so a late transcription of an abandoned recording is discarded.
    private var generation = 0

    init(events: EventHub, modelName: String = Config.sttModel, modelsDir: URL = Config.whisperModelsDir) {
        self.events = events
        self.transcriber = WhisperTranscriber(modelName: modelName, baseFolder: modelsDir)
        if WhisperModelLocator.resolve(name: modelName, in: modelsDir) == nil {
            modelMissing = true
        }
    }

    var modelName: String { transcriber.modelName }

    var isListening: Bool { lock.withLock { listening } }

    func prewarm() async {
        do {
            try await transcriber.load()
            lock.withLock { modelMissing = false }
        } catch {
            lock.withLock { modelMissing = true }
            Log.error("stt: \(error). Download it while online, e.g. models/whisperkit-coreml/openai_whisper-\(modelName); speech input will be cancelled with stt_model_missing")
        }
    }

    func startListening() {
        let alreadyListening = lock.withLock { () -> Bool in
            if listening { return true }
            listening = true
            generation += 1
            return false
        }
        if alreadyListening { return }
        do {
            try capture.start()
            events.emit("listen.started")
        } catch {
            lock.withLock { listening = false }
            Log.error("stt: microphone failed: \(error)")
            events.emit("listen.cancelled", ["reason": "microphone_unavailable"])
        }
    }

    func stopListening() {
        let gen = lock.withLock { () -> Int? in
            guard listening else { return nil }
            listening = false
            return generation
        }
        guard let gen else { return }
        let samples = capture.stop()
        Task.detached { [weak self] in
            await self?.finish(samples: samples, generation: gen)
        }
    }

    func cancelListening(reason: String) {
        let wasListening = lock.withLock { () -> Bool in
            let was = listening
            listening = false
            generation += 1
            return was
        }
        if wasListening { capture.stop() }
        events.emit("listen.cancelled", ["reason": reason])
    }

    // MARK: Transcription

    /// Outcome of processing one recording, before it is turned into an event.
    enum Outcome: Equatable {
        case final(text: String, audioMS: Int, sttMS: Int)
        case cancelled(reason: String)
    }

    /// Transcribes 16 kHz mono samples and applies the drop rules. Also used by tests and benchmarks.
    func process(samples: [Float]) async -> Outcome {
        let audioMS = Int(Double(samples.count) / AudioCapture.sampleRate * 1000)
        if audioMS < Self.minimumAudioMS { return .cancelled(reason: "too_short") }
        if lock.withLock({ modelMissing }) && WhisperModelLocator.resolve(name: modelName, in: transcriber.baseFolder) == nil {
            return .cancelled(reason: "stt_model_missing")
        }
        let start = CFAbsoluteTimeGetCurrent()
        do {
            let text = try await transcriber.transcribe(samples)
            let sttMS = Int((CFAbsoluteTimeGetCurrent() - start) * 1000)
            if text.isEmpty { return .cancelled(reason: "empty_transcript") }
            return .final(text: text, audioMS: audioMS, sttMS: sttMS)
        } catch let error as TranscriberError {
            Log.error("stt: \(error)")
            if case .modelMissing = error {
                lock.withLock { modelMissing = true }
                return .cancelled(reason: "stt_model_missing")
            }
            return .cancelled(reason: "stt_failed")
        } catch {
            Log.error("stt: transcription failed: \(error)")
            return .cancelled(reason: "stt_failed")
        }
    }

    private func finish(samples: [Float], generation gen: Int) async {
        let outcome = await process(samples: samples)
        // A newer recording or a cancel superseded this one.
        guard lock.withLock({ generation == gen }) else { return }
        switch outcome {
        case let .final(text, audioMS, sttMS):
            events.emit("stt.final", [
                "utterance_id": utteranceIDs.next(),
                "text": text,
                "audio_ms": audioMS,
                "stt_ms": sttMS,
            ])
        case let .cancelled(reason):
            events.emit("listen.cancelled", ["reason": reason])
        }
    }
}
