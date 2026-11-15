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
        if let found { UserDefaults.standard.set(found.path, forKey: rememberedRootKey) }
        return found
    }()

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
    static var sttModel: String { string("BEACON_STT_MODEL", "tiny.en") }
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
        return base.appendingPathComponent(raw)
    }

    static var whisperModelsDir: URL { modelsDir.appendingPathComponent("whisperkit-coreml") }
    static var ttsModelsDir: URL { modelsDir.appendingPathComponent("ttskit") }

    static var fixturesDir: URL {
        (repoRoot ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
            .appendingPathComponent("contracts/fixtures")
    }
}
