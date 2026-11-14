// Owner: Mac owner (H5). The SpeechEngine abstraction behind the speech queue (spec 4.3).
import Foundation

/// Cancellation flag shared between the queue and an engine. Handlers registered after cancellation run at once.
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

/// One synthesis backend. The queue calls `speak` for one sentence at a time, never concurrently.
protocol SpeechEngine: AnyObject, Sendable {
    /// `ttskit` or `system`.
    var name: String { get }
    /// Loads models and warms up. Throws if the engine cannot be used.
    func prepare() async throws
    /// Synthesises and plays one sentence. Calls `onFirstAudio` once, when its first sample reaches the output.
    /// Returns when playback finishes or `token` is cancelled; cancellation must silence audio within 100 ms.
    func speak(_ sentence: String, rate: Double, token: SpeechCancelToken, onFirstAudio: @escaping @Sendable () -> Void) async throws
    /// Hard stop of any audio this engine is producing.
    func stopAll()
}
