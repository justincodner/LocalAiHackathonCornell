// Owner: Mac owner. Minimal stderr + file logger for the Helper (logs/helper.log, git-ignored).
import Foundation

enum Log {
    private static let queue = DispatchQueue(label: "dev.beacon.helper.log")
    private static let fileHandle: FileHandle? = {
        let dir = (Config.repoRoot ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
            .appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("helper.log")
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let h = try? FileHandle(forWritingTo: url)
        _ = try? h?.seekToEnd()
        return h
    }()

    static func info(_ message: String) { write("INFO", message) }
    static func warn(_ message: String) { write("WARN", message) }
    static func error(_ message: String) { write("ERROR", message) }

    private static func write(_ level: String, _ message: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) [\(level)] \(message)\n"
        queue.async {
            FileHandle.standardError.write(Data(line.utf8))
            fileHandle?.write(Data(line.utf8))
        }
    }
}
