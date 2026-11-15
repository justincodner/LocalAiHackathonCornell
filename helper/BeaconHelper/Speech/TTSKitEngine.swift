// Owner: Mac owner (H5). TTSKit (Qwen3-TTS 0.6B, voice "ryan", English): renders whole utterances to PCM for ClipPlayer.
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
    let rendersRate = false
    static let voice = Qwen3Speaker.ryan.rawValue
    static let language = Qwen3Language.english.rawValue

    let modelFolder: URL
    let tokenizerFolder: URL
    private let lock = NSLock()
    private var tts: TTSKit?
    private var prepareTask: Task<Void, Error>?
    private var inflight: Task<Void, Never>?

    /// Measurements from `prepare()`, for logs and /health-style reporting.
    private(set) var loadSeconds: Double = 0
    /// Seconds of audio generated per wall-clock second during warm-up.
    private(set) var warmupSpeedFactor: Double = 0
    /// Running estimate of generation speed (audio seconds per wall-clock second), seeded by the warm-up.
    private let speedEstimate = LockedValue(0.0)

    var speedFactor: Double { isLoaded ? speedEstimate.get() : 0 }

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

        // Warm-up generation (not played) to compile ANE graphs and measure generation speed.
        let warmText = "Mail, inbox. Newest is from Sarah Chen about lunch tomorrow."
        let warmStart = CFAbsoluteTimeGetCurrent()
        let warm = try await kit.generate(text: warmText, voice: Self.voice, language: Self.language,
                                          options: Self.options(for: warmText), callback: { _ in true })
        let audioSeconds = Double(warm.audio.count) / Double(warm.sampleRate)
        warmupSpeedFactor = audioSeconds / max(0.001, CFAbsoluteTimeGetCurrent() - warmStart)
        speedEstimate.set(warmupSpeedFactor)
        lock.withLock { self.tts = kit }
        Log.info(String(format: "tts: ttskit loaded in %.2fs; warm-up generated %.2fs of audio at %.2fx real time",
                        loadSeconds, audioSeconds, warmupSpeedFactor))
    }

    static func options(for sentence: String) -> GenerationOptions {
        var options = GenerationOptions()
        options.chunkingStrategy = TextChunkingStrategy.none     // the queue already splits sentences
        options.concurrentWorkerCount = 1
        // 12.5 frames per second of audio; ~15 characters per second of speech. Cap runaway generations.
        options.maxNewTokens = min(GenerationOptions.defaultMaxNewTokens, sentence.count * 2 + 40)
        return options
    }

    func render(_ sentences: [String], rate: Double, token: SpeechCancelToken) async throws -> [[Float]] {
        guard let kit = lock.withLock({ tts }) else { throw TTSKitEngineError.notLoaded }
        // A cancelled render's generation may still be finishing its current step; never overlap two.
        if let previous = lock.withLock({ inflight }) { _ = await previous.value }
        let start = CFAbsoluteTimeGetCurrent()
        let sourceRate = Double(kit.sampleRate)
        var clips: [[Float]] = []
        var audioSeconds = 0.0
        for sentence in sentences {
            if token.isCancelled { return [] }
            let samples = try await generate(kit, sentence, token: token)
            if token.isCancelled { return [] }
            audioSeconds += Double(samples.count) / sourceRate
            clips.append(SpeechAudio.resample(samples, from: sourceRate))
        }
        let wall = CFAbsoluteTimeGetCurrent() - start
        if audioSeconds > 0.5, wall > 0 {
            speedEstimate.set(0.7 * speedEstimate.get() + 0.3 * audioSeconds / wall)
        }
        return clips
    }

    /// One sentence's audio at the model's sample rate. Returns as soon as `token` is cancelled (with no audio),
    /// leaving the generation to stop at its next step.
    private func generate(_ kit: TTSKit, _ sentence: String, token: SpeechCancelToken) async throws -> [Float] {
        let options = Self.options(for: sentence)
        let generation = Task.detached { () -> Result<[Float], Error> in
            do {
                let result = try await kit.generate(text: sentence, voice: Self.voice, language: Self.language,
                                                    options: options, callback: { _ in !token.isCancelled })
                return .success(result.audio)
            } catch {
                return .failure(error)
            }
        }
        lock.withLock { inflight = Task { _ = await generation.value } }
        let once = OnceResume<Result<[Float], Error>>()
        let result = await withCheckedContinuation { (cont: CheckedContinuation<Result<[Float], Error>, Never>) in
            once.set(cont)
            token.onCancel { once.resume(.success([])) }
            Task { once.resume(await generation.value) }
        }
        return try result.get()
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
final class OnceResume<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<T, Never>?
    func set(_ c: CheckedContinuation<T, Never>) { lock.withLock { cont = c } }
    func resume(_ value: T) {
        let c = lock.withLock { () -> CheckedContinuation<T, Never>? in
            let c = cont
            cont = nil
            return c
        }
        c?.resume(returning: value)
    }
}
