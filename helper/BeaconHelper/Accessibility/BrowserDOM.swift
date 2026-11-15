import AppKit

enum BrowserDOM {
    struct Capture {
        let blocks: [JSONObject]
        let partial: Bool
    }

    private static let safari = ["com.apple.Safari", "com.apple.SafariTechnologyPreview"]
    private static let chromium = ["com.google.Chrome", "org.chromium.Chromium",
                                   "com.microsoft.edgemac", "com.brave.Browser"]

    static func bundleID(pid: pid_t) -> String? {
        guard let id = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier,
              safari.contains(id) || chromium.contains(id) else { return nil }
        return id
    }

    static func read(pid: pid_t) async -> Capture? {
        guard Config.bool("BEACON_BROWSER_DOM", false), let id = bundleID(pid: pid),
              let url = Bundle.module.url(forResource: "ReadPage", withExtension: "js"),
              let js = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let command = safari.contains(id) ? "do JavaScript payload in current tab of front window"
            : "execute active tab of front window javascript payload"
        let script = """
            on run argv
              set payload to item 1 of argv
              with timeout of 2 seconds
                tell application id "\(id)"
                  if (count of windows) is 0 then return ""
                  return \(command)
                end tell
              end timeout
            end run
            """
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                continuation.resume(returning: run(script: script, js: js))
            }
        }
    }

    private static func run(script: String, js: String) -> Capture? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script, js]
        let stdout = Pipe()
        process.standardOutput = stdout
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.5, execute: timeout)
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        timeout.cancel()
        guard process.terminationStatus == 0, let json = try? JSONSerialization.jsonObject(with: data),
              let result = json as? JSONObject, let blocks = result["blocks"] as? [JSONObject] else { return nil }
        return Capture(blocks: blocks, partial: result["partial"] as? Bool ?? true)
    }
}
