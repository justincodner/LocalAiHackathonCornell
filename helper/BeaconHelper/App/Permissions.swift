// Owner: Mac owner. Permission checks and first-launch prompts (Microphone, Accessibility, optional Screen Recording).
import ApplicationServices
import AVFoundation
import CoreGraphics
import Foundation
import Network

enum Permissions {
    struct Status {
        let accessibility: Bool
        let microphone: Bool
        let screenRecording: Bool
    }

    static func status() -> Status {
        Status(accessibility: AXIsProcessTrusted(),
               microphone: AVCaptureDevice.authorizationStatus(for: .audio) == .authorized,
               screenRecording: CGPreflightScreenCaptureAccess())
    }

    /// Shows the system prompts for anything not yet granted. Screen Recording is only requested for P2 vision.
    static func requestAtLaunch(includeScreenRecording: Bool = false) {
        if !AXIsProcessTrusted() {
            let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
            _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
        }
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Log.info("microphone permission granted=\(granted)")
            }
        }
        if includeScreenRecording, !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()
        }
    }
}

/// Tracks whether any network path is available, for /health `online` and the HUD Offline badge.
final class Connectivity: @unchecked Sendable {
    static let shared = Connectivity()
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var online = false
    var onChange: (@Sendable (Bool) -> Void)?

    var isOnline: Bool { lock.withLock { online } }

    func start() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            let now = path.status == .satisfied
            let changed: Bool = self.lock.withLock {
                defer { self.online = now }
                return self.online != now
            }
            if changed { self.onChange?(now) }
        }
        monitor.start(queue: DispatchQueue(label: "dev.beacon.helper.net"))
    }
}
