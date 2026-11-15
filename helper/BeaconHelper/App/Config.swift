// Owner: Mac owner. All Helper tunables, read from environment variables (and an optional .env file).
import Foundation

enum Config {
    static let version = "0.1.0"
    static let contractVersion = 1
    static let bundleID = "dev.beacon.helper"
    static let port: UInt16 = 8765

    /// Values from the process environment, falling back to a `.env` file in the repo root or CWD.
    private static let env: [String: String] = {
        var merged: [String: String] = [:]
        for url in dotEnvCandidates() {
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            for raw in text.split(whereSeparator: \.isNewline) {
                var line = String(raw)
                if let hash = line.firstIndex(of: "#") { line = String(line[..<hash]) }
                guard let eq = line.firstIndex(of: "=") else { continue }
                let key = line[..<eq].trimmingCharacters(in: .whitespaces)
                let value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
                if !key.isEmpty, merged[key] == nil { merged[key] = value }
            }
            break
        }
        for (k, v) in ProcessInfo.processInfo.environment { merged[k] = v }
        return merged
    }()

    private static func dotEnvCandidates() -> [URL] {
        var urls: [URL] = []
        if let root = repoRoot { urls.append(root.appendingPathComponent(".env")) }
        urls.append(URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(".env"))
        return urls
    }

    static func string(_ key: String, _ fallback: String) -> String {
        if let v = env[key], !v.isEmpty { return v }
        return fallback
    }

    static func bool(_ key: String, _ fallback: Bool) -> Bool {
        guard let v = env[key]?.lowercased() else { return fallback }
        return ["1", "true", "yes", "on"].contains(v)
    }

    /// The repository root: BEACON_ROOT, or the nearest ancestor of the executable or CWD holding `contracts/`,
    /// or else the root remembered from the last launch that knew it. The remembered root matters when Beacon
    /// Helper is reopened from /Applications or the Dock: then there is no BEACON_ROOT and no repo above the app,
    /// and without the root the Helper cannot find the Whisper model, so every recording was rejected.
    static let repoRoot: URL? = {
        let found = resolveRoot(environment: ProcessInfo.processInfo.environment["BEACON_ROOT"],
                                starts: [URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
                                         Bundle.main.executableURL?.deletingLastPathComponent()].compactMap { $0 },
                                remembered: UserDefaults.standard.string(forKey: rememberedRootKey))
        // Remember the main checkout, not a worktree: worktrees come and go, and have no models/ of their own.
        if let found { UserDefaults.standard.set((mainCheckout(of: found) ?? found).path, forKey: rememberedRootKey) }
        return found
    }()

    /// The main checkout when `root` is a linked git worktree (its `.git` is a file "gitdir: <main>/.git/worktrees/<name>"),
    /// else nil. Git-ignored files such as `models/` live only in the main checkout.
    static func mainCheckout(of root: URL, readGitFile: (URL) -> String? = { try? String(contentsOf: $0, encoding: .utf8) }) -> URL? {
        guard let text = readGitFile(root.appendingPathComponent(".git")) else { return nil }
        guard let line = text.split(whereSeparator: \.isNewline).first, line.hasPrefix("gitdir:") else { return nil }
        var gitdir = line.dropFirst("gitdir:".count).trimmingCharacters(in: .whitespaces)
        if !gitdir.hasPrefix("/") { gitdir = root.appendingPathComponent(gitdir).standardizedFileURL.path }
        guard let marker = gitdir.range(of: "/.git/worktrees/") else { return nil }
        let main = String(gitdir[..<marker.lowerBound])
        return main.isEmpty ? nil : URL(fileURLWithPath: main)
    }

    static let rememberedRootKey = "BeaconRepoRoot"

    /// Pure lookup for `repoRoot`, so the order can be tested.
    static func resolveRoot(environment: String?, starts: [URL], remembered: String?,
                            isRepo: (URL) -> Bool = { FileManager.default.fileExists(
                                atPath: $0.appendingPathComponent("contracts").path) }) -> URL? {
        if let environment, !environment.isEmpty { return URL(fileURLWithPath: environment) }
        for start in starts {
            var dir = start.standardizedFileURL
            for _ in 0..<8 {
                if isRepo(dir) { return dir }
                let parent = dir.deletingLastPathComponent()
                if parent.path == dir.path { break }
                dir = parent
            }
        }
        if let remembered {
            let url = URL(fileURLWithPath: remembered)
            if isRepo(url) { return url }
        }
        return nil
    }

    /// `system` by default: TTSKit renders at only ~0.4-0.8x real time on the 8 GB M1, too slow for render-then-play.
    static var tts: String { string("BEACON_TTS", "system") }
    /// Whisper models Beacon is set up for, most accurate first (scripts/download_whisper_models.py fetches them).
    /// small.en is the default; base.en and tiny.en are faster and less accurate.
    static let sttModelChoices = ["small.en", "base.en", "tiny.en"]
    static var defaultSTTModel: String { sttModelChoices[0] }

    /// Models to try, in order: just BEACON_STT_MODEL when it is set, else every choice.
    static var sttModelCandidates: [String] {
        let configured = string("BEACON_STT_MODEL", "")
        return configured.isEmpty ? sttModelChoices : [configured]
    }

    /// BEACON_STT_MODEL if set. Otherwise the first installed choice, so a Mac that only has tiny.en keeps
    /// working; with none installed, the default (its name then appears in the stt_model_missing log).
    static var sttModel: String { pickSTTModel(candidates: sttModelCandidates, in: whisperModelsDir) }

    /// Pure, so it is tested: the first candidate whose model folder is in `dir`, else the first candidate.
    static func pickSTTModel(candidates: [String], in dir: URL,
                             isInstalled: ((String, URL) -> Bool)? = nil) -> String {
        let installed = isInstalled ?? { name, dir in WhisperModelLocator.resolve(name: name, in: dir) != nil }
        return candidates.first { installed($0, dir) } ?? candidates.first ?? defaultSTTModel
    }
    /// Feed visible on-screen words to Whisper as a decoding prompt (BEACON_STT_SCREEN_HINTS=0 turns it off).
    static var sttScreenHints: Bool { bool("BEACON_STT_SCREEN_HINTS", true) }
    static var pttKey: String { string("BEACON_PTT_KEY", "right_option") }
    static var devLAN: Bool { bool("BEACON_DEV_LAN", false) }
    static var token: String { string("BEACON_TOKEN", "") }

    /// Root folder for local model files. Never downloaded at runtime except the explicit first-run TTSKit fetch.
    static var modelsDir: URL {
        let raw = string("BEACON_MODELS_DIR", "models")
        if raw.hasPrefix("/") { return URL(fileURLWithPath: raw) }
        let base = repoRoot ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        return resolveModelsDir(relative: raw, root: base, mainCheckout: mainCheckout(of: base),
                                sttModels: sttModelCandidates)
    }

    /// `<root>/models`, unless `root` is a worktree without any of the Whisper models and the main checkout has one.
    /// Started from a worktree, the Helper otherwise cancelled every recording with stt_model_missing. Pure, so it is tested.
    static func resolveModelsDir(relative: String, root: URL, mainCheckout: URL?, sttModels: [String],
                                 hasWhisperModel: ((URL) -> Bool)? = nil) -> URL {
        let own = root.appendingPathComponent(relative)
        guard let mainCheckout, mainCheckout.standardizedFileURL.path != root.standardizedFileURL.path else { return own }
        let shared = mainCheckout.appendingPathComponent(relative)
        let has = hasWhisperModel ?? { dir in
            sttModels.contains { WhisperModelLocator.resolve(name: $0, in: dir.appendingPathComponent("whisperkit-coreml")) != nil }
        }
        if has(own) { return own }
        return has(shared) ? shared : own
    }

    static var whisperModelsDir: URL { modelsDir.appendingPathComponent("whisperkit-coreml") }
    static var ttsModelsDir: URL { modelsDir.appendingPathComponent("ttskit") }

    static var fixturesDir: URL {
        (repoRoot ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
            .appendingPathComponent("contracts/fixtures")
    }
}
