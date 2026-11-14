// Owner: Mac owner. Entry point.
//   beacon-helper                         run the menu-bar Helper server (default)
//   beacon-helper dump --out <file.json>  save a snapshot of the frontmost window as a fixture (spec 5.7)
//              [--scope window|focused|menu] [--delay <seconds>] [--max <n>]
import AppKit
import Foundation

let arguments = Array(CommandLine.arguments.dropFirst())

func argValue(_ name: String) -> String? {
    guard let i = arguments.firstIndex(of: name), i + 1 < arguments.count else { return nil }
    return arguments[i + 1]
}

if arguments.first == "dump" {
    guard let out = argValue("--out") else {
        FileHandle.standardError.write(Data("usage: beacon-helper dump --out <file.json> [--scope window] [--delay 3] [--max 250]\n".utf8))
        exit(2)
    }
    let delay = Double(argValue("--delay") ?? "0") ?? 0
    let scope = argValue("--scope") ?? "window"
    let maxElements = Int(argValue("--max") ?? "250") ?? 250
    guard AXIsProcessTrusted() else {
        FileHandle.standardError.write(Data("Accessibility permission is not granted to this binary.\n".utf8))
        exit(1)
    }
    if delay > 0 {
        FileHandle.standardError.write(Data("Capturing in \(delay)s; switch to the target app now.\n".utf8))
        Thread.sleep(forTimeInterval: delay)
    }
    let semaphore = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var exitCode: Int32 = 0
    Task.detached {
        do {
            var snap = try await AccessibilityService().snapshot(scope: scope, maxElements: maxElements)
            snap["ok"] = true
            let url = URL(fileURLWithPath: out)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: JSON.sanitize(snap), options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
            try data.write(to: url)
            print("wrote \(url.path): \(snap["app"] ?? "?") / \(snap["window"] ?? "?"), \(snap["total_elements"] ?? 0) elements, \(snap["build_ms"] ?? 0) ms")
        } catch {
            FileHandle.standardError.write(Data("dump failed: \(error)\n".utf8))
            exitCode = 1
        }
        semaphore.signal()
    }
    semaphore.wait()
    exit(exitCode)
}

let app = NSApplication.shared
let delegate = MainActor.assumeIsolated { AppDelegate() }
app.delegate = delegate
app.run()
