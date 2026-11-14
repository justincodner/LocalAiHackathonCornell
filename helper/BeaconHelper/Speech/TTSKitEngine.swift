// Owner: Mac owner (H5). TTSKit (Qwen3-TTS 0.6B, voice "ryan", English) with streaming playback through StreamingPlayer.
import Foundation
import TTSKit

enum TTSKitEngineError: Error, CustomStringConvertible {
    case modelMissing(String)
    case notLoaded

    var description: String {
        switch self {
        case .modelMissing(let detail): return "ttskit model missing: \(detail)"
        case .notLoaded: return "ttskit not loaded"
        }
    }
}

final class TTSKitEngine: SpeechEngine, @unchecked Sendable {
    let name = "ttskit"
    static let voice = Qwen3Speaker.ryan.rawValue
    static let language = Qwen3Language.english.rawValue

    let modelFolder: URL
    let tokenizerFolder: URL
    private let lock = NSLock()
    private var tts: TTSKit?
    private var player: StreamingPlayer?
    private var prepareTask: Task<Void, Error>?
    private var inflight: Task<Void, Never>?

    /// Measurements from `prepare()`, for logs and /health-style reporting.
    private(set) var loadSeconds: Double = 0
    private(set) var warmupFirstAudioSeconds: Double = 0
    /// Seconds of audio generated per wall-clock second during warm-up (above 1 streams without gaps).
    private(set) var warmupSpeedFactor: Double = 0
    /// Time to first audio of the most recent real sentence.
    private(set) var lastFirstAudioSeconds: Double = 0
    /// Running estimate of generation speed (audio seconds per wall-clock second), seeded by the warm-up.
    private let speedEstimate = LockedValue(1.0)

    /// Upper bound on the pre-buffer. On the M1 TTSKit generates at ~0.8x real time, so a fully gap-free start
    /// would need over 1 s of buffering for long sentences; capping it keeps time to first audio near the
    /// 1 s budget at the cost of a possible short pause late in a long sentence.
    static let maxPreBuffer = 0.5

    /// Pre-buffer so playback at `rate` does not run dry when generation is slower than playback.
    /// Expected audio length is ~0.075 s per character at normal speed.
    static func preBuffer(for sentence: String, rate: Double, speedFactor: Double) -> Double {
        let expectedAudio = max(0.5, Double(sentence.count) * 0.075)
        let deficit = max(0, 1 - speedFactor / max(rate, 0.1))
        return min(maxPreBuffer, 0.12 + expectedAudio * deficit)
    }

    /// Called with each real sentence's time to first audio (seconds), so the service can fall back if it is slow.
    var onTimeToFirstAudio: (@Sendable (Double) -> Void)?

    init(modelFolder: URL = Config.ttsModelsDir,
         tokenizerFolder: URL = Config.ttsModelsDir.appendingPathComponent("tokenizer")) {
        self.modelFolder = modelFolder
        self.tokenizerFolder = tokenizerFolder
    }

    /// True if every component and the tokenizer are on disk, so loading never touches the network.
    static func filesPresent(modelFolder: URL, tokenizerFolder: URL) -> String? {
        let fm = FileManager.default
        let config = TTSKitConfig(model: .qwen3TTS_0_6b, modelFolder: modelFolder)
        let components: [(String, String)] = [
            ("text_projector", config.textProjectorVariant), ("code_embedder", config.codeEmbedderVariant),
            ("multi_code_embedder", config.multiCodeEmbedderVariant), ("code_decoder", config.codeDecoderVariant),
            ("multi_code_decoder", config.multiCodeDecoderVariant), ("speech_decoder", config.speechDecoderVariant),
        ]
        for (component, variant) in components where config.modelURL(component: component, variant: variant) == nil {
            return "\(component)/\(config.versionDir)/\(variant) not found under \(modelFolder.path)"
        }
        if !fm.fileExists(atPath: tokenizerFolder.appendingPathComponent("tokenizer.json").path) {
            return "tokenizer.json not found in \(tokenizerFolder.path)"
        }
        return nil
    }

    var isLoaded: Bool { lock.withLock { tts != nil } }

    func prepare() async throws {
        let task = lock.withLock { () -> Task<Void, Error> in
            if let prepareTask { return prepareTask }
            let t = Task { try await self.load() }
            prepareTask = t
            return t
        }
        do {
            try await task.value
        } catch {
            lock.withLock { prepareTask = nil }
            throw error
        }
    }

    private func load() async throws {
        if let missing = Self.filesPresent(modelFolder: modelFolder, tokenizerFolder: tokenizerFolder) {
            throw TTSKitEngineError.modelMissing(missing)
        }
        let start = CFAbsoluteTimeGetCurrent()
        let config = TTSKitConfig(
            model: .qwen3TTS_0_6b,
            modelFolder: modelFolder,
            tokenizerFolder: tokenizerFolder,
            verbose: false,
            logLevel: .error,
            download: false,
            load: true
        )
        let kit = try await TTSKit(config)
        // Prefill cache for the fixed voice/language: removes most prefill cost from every later sentence.
        try await kit.buildPromptCache(voice: Self.voice, language: Self.language)
        loadSeconds = CFAbsoluteTimeGetCurrent() - start

        // Warm-up generation (not played) to compile ANE graphs and measure time to first audio.
        let warmStart = CFAbsoluteTimeGetCurrent()
        let firstAudio = StartedFlag()
        let firstAt = LockedValue<Double>(0)
        let warm = try await kit.generate(text: "Mail, inbox. Newest is from Sarah Chen about lunch tomorrow.", voice: Self.voice, language: Self.language,
                                   options: Self.options(for: "Mail, inbox. Newest is from Sarah Chen about lunch tomorrow."),
                                   callback: { progress in
                                       if !progress.audio.isEmpty, firstAudio.setOnce() {
                                           firstAt.set(CFAbsoluteTimeGetCurrent() - warmStart)
                                       }
                                       return true
                                   })
        warmupFirstAudioSeconds = firstAt.get()
        let audioSeconds = Double(warm.audio.count) / Double(warm.sampleRate)
        warmupSpeedFactor = audioSeconds / max(0.001, CFAbsoluteTimeGetCurrent() - warmStart)
        speedEstimate.set(warmupSpeedFactor)
        Log.info(String(format: "tts: warm-up generated %.2fs of audio at %.2fx real time", audioSeconds, warmupSpeedFactor))

        let player = StreamingPlayer(sampleRate: Double(kit.sampleRate))
        try player.startEngine()
        lock.withLock {
            self.tts = kit
            self.player = player
        }
        Log.info(String(format: "tts: ttskit loaded in %.2fs, warm-up time to first audio %.0f ms",
                        loadSeconds, warmupFirstAudioSeconds * 1000))
    }

    static func options(for sentence: String) -> GenerationOptions {
        var options = GenerationOptions()
        options.chunkingStrategy = TextChunkingStrategy.none     // the queue already splits sentences
        options.concurrentWorkerCount = 1
        // 12.5 frames per second of audio; ~15 characters per second of speech. Cap runaway generations.
        options.maxNewTokens = min(GenerationOptions.defaultMaxNewTokens, sentence.count * 2 + 40)
        return options
    }

    func speak(_ sentence: String, rate: Double, token: SpeechCancelToken,
               onFirstAudio: @escaping @Sendable () -> Void) async throws {
        guard let (kit, player) = lock.withLock({ tts.flatMap { t in player.map { (t, $0) } } }) else {
            throw TTSKitEngineError.notLoaded
        }
        if token.isCancelled { return }
        let start = CFAbsoluteTimeGetCurrent()
        let sentStart = StartedFlag()
        let firstAudioWrapped: @Sendable () -> Void = { [weak self] in
            if sentStart.setOnce() {
                let ttfa = CFAbsoluteTimeGetCurrent() - start
                self?.lastFirstAudioSeconds = ttfa
                self?.onTimeToFirstAudio?(ttfa)
            }
            onFirstAudio()
        }
        // A cancelled sentence's generation may still be finishing its current step; never overlap two.
        if let previous = lock.withLock({ inflight }) { _ = await previous.value }
        if token.isCancelled { return }

        let preBuffer = Self.preBuffer(for: sentence, rate: rate, speedFactor: speedEstimate.get())
        try player.begin(rate: rate, preBuffer: preBuffer, onFirstAudio: firstAudioWrapped)

        let options = Self.options(for: sentence)
        let sampleRate = Double(kit.sampleRate)
        let speed = speedEstimate
        let generation = Task.detached { () -> Error? in
            let genStart = CFAbsoluteTimeGetCurrent()
            do {
                let result = try await kit.generate(
                    text: sentence, voice: Self.voice, language: Self.language, options: options,
                    callback: { progress in
                        if token.isCancelled { return false }
                        player.append(progress.audio)
                        return !token.isCancelled
                    })
                // Track generation speed (audio seconds per wall second) to size the next pre-buffer.
                let audioSeconds = Double(result.audio.count) / sampleRate
                let wall = CFAbsoluteTimeGetCurrent() - genStart
                if !token.isCancelled, audioSeconds > 0.5, wall > 0 {
                    speed.set(0.7 * speed.get() + 0.3 * audioSeconds / wall)
                }
                return nil
            } catch {
                return error
            }
        }
        let inflightTask = Task { _ = await generation.value }
        lock.withLock { inflight = inflightTask }

        // Return as soon as generation ends or the token is cancelled, whichever comes first, so a stop
        // finishes the item at once instead of waiting for the current generation step.
        let once = OnceResume()
        let error: Error? = await withCheckedContinuation { (cont: CheckedContinuation<Error?, Never>) in
            once.set(cont)
            token.onCancel {
                player.stopNow()
                once.resume(nil)
            }
            Task { once.resume(await generation.value) }
        }
        if token.isCancelled { return }
        if let error {
            player.stopNow()
            throw error
        }
        await player.finishAndWait()
    }

    func stopAll() {
        lock.withLock { player }?.stopNow()
    }
}

/// Tiny lock-protected box for values written from TTSKit callbacks.
final class LockedValue<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T
    init(_ value: T) { self.value = value }
    func get() -> T { lock.withLock { value } }
    func set(_ v: T) { lock.withLock { value = v } }
}

/// Resumes a continuation exactly once, from whichever caller gets there first.
/// `set` is called synchronously inside `withCheckedContinuation`, before any `resume` can run.
final class OnceResume: @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<Error?, Never>?
    func set(_ c: CheckedContinuation<Error?, Never>) { lock.withLock { cont = c } }
    func resume(_ value: Error?) {
        let c = lock.withLock { () -> CheckedContinuation<Error?, Never>? in
            let c = cont
            cont = nil
            return c
        }
        c?.resume(returning: value)
    }
}
