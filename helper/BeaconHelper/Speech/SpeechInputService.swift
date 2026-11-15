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
    /// Reads the frontmost window's visible text; nil disables screen hints.
    private let screenText: (@Sendable () async -> ScreenText?)?
    /// The hint being built for the current recording, started on key press.
    private var hintTask: Task<String, Never>?
    /// How long to wait after key release for a still-running screen read before transcribing without it.
    static let hintWaitSeconds = 0.25

    init(events: EventHub, modelName: String = Config.sttModel, modelsDir: URL = Config.whisperModelsDir,
         screenText: (@Sendable () async -> ScreenText?)? = nil) {
        self.events = events
        self.screenText = screenText
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
        startHint()
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
        let hintTask = lock.withLock { () -> Task<String, Never>? in
            defer { self.hintTask = nil }
            return self.hintTask
        }
        Task.detached { [weak self] in
            let hint = await Self.awaitHint(hintTask)
            await self?.finish(samples: samples, hint: hint, generation: gen)
        }
    }

    func cancelListening(reason: String) {
        let wasListening = lock.withLock { () -> Bool in
            let was = listening
            listening = false
            generation += 1
            hintTask?.cancel()
            hintTask = nil
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
    func process(samples: [Float], hint: String? = nil) async -> Outcome {
        let audioMS = Int(Double(samples.count) / AudioCapture.sampleRate * 1000)
        if audioMS < Self.minimumAudioMS { return .cancelled(reason: "too_short") }
        if lock.withLock({ modelMissing }) && WhisperModelLocator.resolve(name: modelName, in: transcriber.baseFolder) == nil {
            return .cancelled(reason: "stt_model_missing")
        }
        let start = CFAbsoluteTimeGetCurrent()
        do {
            let text = try await transcriber.transcribe(samples, hint: hint)
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

    private func finish(samples: [Float], hint: String?, generation gen: Int) async {
        let outcome = await process(samples: samples, hint: hint)
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

    // MARK: Screen hints

    /// Reads the frontmost window off the calling thread so the microphone opens without waiting for it.
    private func startHint() {
        guard let screenText else { return }
        let task = Task.detached(priority: .userInitiated) { () -> String in
            let started = CFAbsoluteTimeGetCurrent()
            guard let screen = await screenText(), !Task.isCancelled else { return "" }
            let hint = ScreenVocabulary.hint(for: screen)
            Log.info(String(format: "stt: screen hint from %@ in %.0f ms: %@", screen.app,
                            (CFAbsoluteTimeGetCurrent() - started) * 1000, hint.isEmpty ? "(none)" : hint))
            return hint
        }
        lock.withLock {
            hintTask?.cancel()
            hintTask = task
        }
    }

    /// The hint if it is ready within `hintWaitSeconds`, else nil (transcribe without a prompt).
    /// A late screen read is abandoned, not awaited: the AX walk cannot be interrupted.
    static func awaitHint(_ task: Task<String, Never>?, timeout: Double = hintWaitSeconds) async -> String? {
        guard let task else { return nil }
        let hint: String? = await withCheckedContinuation { (cont: CheckedContinuation<String?, Never>) in
            let once = OnceFlag()
            Task {
                let value = await task.value
                if once.claim() { cont.resume(returning: value) }
            }
            Task {
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                if once.claim() {
                    Log.info("stt: screen hint not ready in time; transcribing without it")
                    cont.resume(returning: nil)
                }
            }
        }
        guard let hint, !hint.isEmpty else { return nil }
        return hint
    }
}

/// Lets exactly one of several racing callers win.
final class OnceFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var claimed = false
    func claim() -> Bool {
        lock.withLock {
            if claimed { return false }
            claimed = true
            return true
        }
    }
}
