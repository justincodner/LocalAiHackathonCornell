// Owner: Mac owner (H5). The SpeechEngine abstraction behind the speech queue (spec 4.3).
import Foundation

/// Cancellation flag shared between the queue, an engine and the player. Handlers registered after cancellation run at once.
final class SpeechCancelToken: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    private var handlers: [() -> Void] = []

    var isCancelled: Bool { lock.withLock { cancelled } }

    func cancel() {
        let toRun = lock.withLock { () -> [() -> Void] in
            guard !cancelled else { return [] }
            cancelled = true
            let h = handlers
            handlers.removeAll()
            return h
        }
        toRun.forEach { $0() }
    }

    /// Runs `handler` when the token is cancelled (immediately if it already is).
    func onCancel(_ handler: @escaping () -> Void) {
        let runNow = lock.withLock { () -> Bool in
            if cancelled { return true }
            handlers.append(handler)
            return false
        }
        if runNow { handler() }
    }
}

/// One synthesis backend. It only renders audio; `ClipPlayer` plays it. The queue never calls `render` concurrently.
protocol SpeechEngine: AnyObject, Sendable {
    /// `ttskit` or `system`.
    var name: String { get }
    /// True when `render` already speaks at the requested rate (system voice); false when the player must
    /// time-stretch the audio for rates other than 1.
    var rendersRate: Bool { get }
    /// Recently measured render speed in audio seconds per wall-clock second; 0 when not ready or unknown.
    var speedFactor: Double { get }
    /// Loads models and warms up. Throws if the engine cannot be used.
    func prepare() async throws
    /// Renders a whole utterance to mono Float32 PCM at `SpeechAudio.sampleRate`, one clip per piece (a sentence,
    /// or the whole text for engines that render it in one go). Must return promptly once `token` is cancelled.
    func render(_ sentences: [String], rate: Double, token: SpeechCancelToken) async throws -> [[Float]]
}
