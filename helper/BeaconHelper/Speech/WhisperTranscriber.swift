// Owner: Mac owner (H4). WhisperKit wrapper: loads a local CoreML Whisper model (never downloads) and transcribes 16 kHz audio.
import Foundation
import WhisperKit

enum WhisperModelLocator {
    /// Folder names to try for a configured model name, so `tiny.en`, `openai_whisper-tiny.en`
    /// and `large-v3-v20240930_626MB` all resolve to the folders published in argmaxinc/whisperkit-coreml.
    static func candidateNames(for name: String) -> [String] {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        var names = [trimmed]
        let prefixes = ["openai_whisper-", "distil-whisper_"]
        if !prefixes.contains(where: { trimmed.hasPrefix($0) }) {
            names.append("openai_whisper-" + trimmed)
            names.append("distil-whisper_" + trimmed)
        }
        return names
    }

    /// The first candidate folder under `base` that holds the three compiled Whisper models.
    static func resolve(name: String, in base: URL, fileManager: FileManager = .default) -> URL? {
        for candidate in candidateNames(for: name) {
            let folder = base.appendingPathComponent(candidate)
            if isModelFolder(folder, fileManager: fileManager) { return folder }
        }
        return nil
    }

    static func isModelFolder(_ folder: URL, fileManager: FileManager = .default) -> Bool {
        ["AudioEncoder", "TextDecoder", "MelSpectrogram"].allSatisfy { component in
            fileManager.fileExists(atPath: folder.appendingPathComponent("\(component).mlmodelc").path)
                || fileManager.fileExists(atPath: folder.appendingPathComponent("\(component).mlpackage").path)
        }
    }

    /// Where the tokenizer lives: inside the model folder (we copy it there), else `<base>/tokenizers/<folder>`.
    static func tokenizerFolder(for modelFolder: URL, fileManager: FileManager = .default) -> URL? {
        let candidates = [
            modelFolder,
            modelFolder.deletingLastPathComponent().appendingPathComponent("tokenizers")
                .appendingPathComponent(modelFolder.lastPathComponent),
        ]
        return candidates.first { fileManager.fileExists(atPath: $0.appendingPathComponent("tokenizer.json").path) }
    }
}

enum TranscriberError: Error, CustomStringConvertible {
    case modelMissing(String)
    case notLoaded

    var description: String {
        switch self {
        case .modelMissing(let detail): return "stt model missing: \(detail)"
        case .notLoaded: return "stt model not loaded"
        }
    }
}

/// Serialises access to one WhisperKit pipeline.
actor WhisperTranscriber {
    let modelName: String
    let baseFolder: URL
    private var pipe: WhisperKit?
    private var loadTask: Task<KitBox, Error>?
    private(set) var loadSeconds: Double = 0
    private(set) var resolvedFolder: URL?

    init(modelName: String, baseFolder: URL) {
        self.modelName = modelName
        self.baseFolder = baseFolder
    }

    var isLoaded: Bool { pipe != nil }

    /// Loads the model from disk. Throws `TranscriberError.modelMissing` if the folder or tokenizer is absent.
    func load() async throws {
        _ = try await pipeline()
    }

    private func pipeline() async throws -> WhisperKit {
        if let pipe { return pipe }
        if let loadTask { return try await loadTask.value.kit }

        guard let folder = WhisperModelLocator.resolve(name: modelName, in: baseFolder) else {
            throw TranscriberError.modelMissing(
                "no Whisper model folder for '\(modelName)' under \(baseFolder.path) (tried \(WhisperModelLocator.candidateNames(for: modelName).joined(separator: ", ")))")
        }
        guard let tokenizer = WhisperModelLocator.tokenizerFolder(for: folder) else {
            // Without a local tokenizer WhisperKit would try the network; refuse instead.
            throw TranscriberError.modelMissing("no tokenizer.json in \(folder.path)")
        }
        resolvedFolder = folder

        let task = Task { () throws -> KitBox in
            let start = CFAbsoluteTimeGetCurrent()
            let config = WhisperKitConfig(
                modelFolder: folder.path,
                tokenizerFolder: tokenizer,
                verbose: false,
                logLevel: .error,
                prewarm: false,
                load: true,
                download: false
            )
            let kit = try await WhisperKit(config)
            let loaded = CFAbsoluteTimeGetCurrent() - start
            // Warm up so the first real request is fast: silence alone leaves the decoder path cold,
            // so also run two seconds of a voice-like warble that makes Whisper emit tokens.
            _ = try? await kit.transcribe(audioArray: Self.warmupAudio(), decodeOptions: Self.decodeOptions)
            _ = try? await kit.transcribe(audioArray: Self.warmupAudio(), decodeOptions: Self.decodeOptions)
            let elapsed = CFAbsoluteTimeGetCurrent() - start
            Log.info(String(format: "stt: loaded %@ in %.2fs (model load %.2fs, warm-up %.2fs)",
                            folder.lastPathComponent, elapsed, loaded, elapsed - loaded))
            return KitBox(kit: kit)
        }
        loadTask = task
        let start = CFAbsoluteTimeGetCurrent()
        do {
            let kit = try await task.value.kit
            loadSeconds = CFAbsoluteTimeGetCurrent() - start
            pipe = kit
            loadTask = nil
            return kit
        } catch {
            loadTask = nil
            throw error
        }
    }

    /// Two seconds of an amplitude-modulated, pitch-gliding tone sampled at 16 kHz.
    static func warmupAudio() -> [Float] {
        let n = 32_000
        var out = [Float](repeating: 0, count: n)
        var phase: Double = 0
        for i in 0..<n {
            let t = Double(i) / 16_000
            let f0 = 140 + 40 * sin(2 * .pi * 0.7 * t)
            phase += 2 * .pi * f0 / 16_000
            let envelope = 0.5 + 0.5 * sin(2 * .pi * 4 * t)
            let harmonics = sin(phase) + 0.5 * sin(2 * phase) + 0.25 * sin(3 * phase)
            out[i] = Float(0.2 * envelope * harmonics)
        }
        return out
    }

    static var decodeOptions: DecodingOptions {
        DecodingOptions(
            verbose: false,
            task: .transcribe,
            language: "en",
            temperature: 0,
            temperatureFallbackCount: 2,
            usePrefillPrompt: true,
            detectLanguage: false,
            skipSpecialTokens: true,
            withoutTimestamps: true,
            suppressBlank: true
        )
    }

    /// Transcribes 16 kHz mono samples and returns the cleaned text (possibly empty).
    func transcribe(_ samples: [Float]) async throws -> String {
        let kit = try await pipeline()
        let results = try await kit.transcribe(audioArray: samples, decodeOptions: Self.decodeOptions)
        let raw = results.map(\.text).joined(separator: " ")
        return TranscriptCleaner.clean(raw)
    }
}

/// Carries the (non-Sendable) WhisperKit pipeline out of its load task; only the actor touches it afterwards.
final class KitBox: @unchecked Sendable {
    let kit: WhisperKit
    init(kit: WhisperKit) { self.kit = kit }
}
