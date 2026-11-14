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

    /// The repository root: BEACON_ROOT, or the nearest ancestor of the executable or CWD holding `contracts/`.
    static let repoRoot: URL? = {
        if let explicit = ProcessInfo.processInfo.environment["BEACON_ROOT"] {
            return URL(fileURLWithPath: explicit)
        }
        let starts = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
            Bundle.main.executableURL?.deletingLastPathComponent(),
        ].compactMap { $0 }
        for start in starts {
            var dir = start.standardizedFileURL
            for _ in 0..<8 {
                if FileManager.default.fileExists(atPath: dir.appendingPathComponent("contracts").path) { return dir }
                let parent = dir.deletingLastPathComponent()
                if parent.path == dir.path { break }
                dir = parent
            }
        }
        return nil
    }()

    static var tts: String { string("BEACON_TTS", "ttskit") }
    static var sttModel: String { string("BEACON_STT_MODEL", "tiny.en") }
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
