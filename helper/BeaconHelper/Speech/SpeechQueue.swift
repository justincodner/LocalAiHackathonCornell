// Owner: Mac owner (H5). The single speech queue: sentence-by-sentence synthesis, interrupt and stop semantics.
//
// - `enqueue(interrupt: false)` appends; `interrupt: true` flushes queued items and stops the current one first.
// - `stop()` flushes everything and silences the current sentence immediately (the engine stops its player).
// - Every item emits `tts.started {speech_id}` at its first audio sample (if it got that far) and exactly one
//   `tts.finished {speech_id, interrupted}`, including items flushed before they started.
import Foundation

final class SpeechQueue: @unchecked Sendable {
    struct Item {
        let id: String
        let text: String
        let sentences: [String]
        let rate: Double
        let token = SpeechCancelToken()
    }

    typealias Emit = @Sendable (_ type: String, _ fields: JSONObject) -> Void

    private let lock = NSLock()
    private var pending: [Item] = []
    private var current: Item?
    private var workerRunning = false
    private let ids: SequentialID
    private let engineProvider: @Sendable () -> SpeechEngine
    private let fallback: @Sendable (SpeechEngine, Error) -> SpeechEngine?
    private let emit: Emit
    private let emitStarted: @Sendable (String, String) -> Void

    /// - Parameters:
    ///   - engineProvider: the engine to use for the next sentence (asked per sentence, so fallbacks apply at once).
    ///   - fallback: called when an engine throws; return another engine to retry the sentence, or nil to skip it.
    init(ids: SequentialID = SequentialID(prefix: "sp"),
         engineProvider: @escaping @Sendable () -> SpeechEngine,
         fallback: @escaping @Sendable (SpeechEngine, Error) -> SpeechEngine? = { _, _ in nil },
         emitStarted: (@Sendable (String, String) -> Void)? = nil,
         emit: @escaping Emit) {
        self.ids = ids
        self.engineProvider = engineProvider
        self.fallback = fallback
        self.emit = emit
        self.emitStarted = emitStarted ?? { id, _ in emit("tts.started", ["speech_id": id]) }
    }

    /// True while an item is being synthesised or played, or items are waiting.
    var isSpeaking: Bool { lock.withLock { current != nil || !pending.isEmpty } }

    /// Number of items waiting behind the current one.
    var pendingCount: Int { lock.withLock { pending.count } }

    @discardableResult
    func enqueue(text: String, interrupt: Bool, rate: Double?) -> String {
        let item = Item(id: ids.next(), text: text, sentences: SentenceSplitter.split(text), rate: SpeechRate.clamp(rate))
        var flushed: [Item] = []
        var toCancel: Item?
        let startWorker = lock.withLock { () -> Bool in
            if interrupt {
                flushed = pending
                pending.removeAll()
                toCancel = current
            }
            pending.append(item)
            if workerRunning { return false }
            workerRunning = true
            return true
        }
        toCancel?.token.cancel()
        for f in flushed { emit("tts.finished", ["speech_id": f.id, "interrupted": true]) }
        if startWorker {
            Task.detached { [self] in await runWorker() }
        }
        return item.id
    }

    /// Flushes the queue and silences the current item.
    func stop() {
        let (flushed, active) = lock.withLock { () -> ([Item], Item?) in
            let f = pending
            pending.removeAll()
            return (f, current)
        }
        active?.token.cancel()
        // Belt and braces: silence the engine even if the item has not wired its token yet.
        if active != nil { engineProvider().stopAll() }
        for f in flushed { emit("tts.finished", ["speech_id": f.id, "interrupted": true]) }
    }

    private func runWorker() async {
        while true {
            let next = lock.withLock { () -> Item? in
                guard !pending.isEmpty else {
                    current = nil
                    workerRunning = false
                    return nil
                }
                let item = pending.removeFirst()
                current = item
                return item
            }
            guard let item = next else { return }
            await play(item)
            lock.withLock { current = nil }
            emit("tts.finished", ["speech_id": item.id, "interrupted": item.token.isCancelled])
        }
    }

    private func play(_ item: Item) async {
        let started = StartedFlag()
        let emitStarted = self.emitStarted
        let onFirstAudio: @Sendable () -> Void = {
            if started.setOnce() { emitStarted(item.id, item.text) }
        }
        for sentence in item.sentences {
            if item.token.isCancelled { break }
            var engine = engineProvider()
            var attempts = 0
            while attempts < 2 {
                attempts += 1
                do {
                    try await engine.speak(sentence, rate: item.rate, token: item.token, onFirstAudio: onFirstAudio)
                    break
                } catch {
                    if item.token.isCancelled { break }
                    Log.warn("tts: \(engine.name) failed on a sentence: \(error)")
                    guard let replacement = fallback(engine, error), replacement !== engine else { break }
                    engine = replacement
                }
            }
        }
    }
}

/// Set-once flag safe to touch from audio callbacks.
final class StartedFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var isSet: Bool { lock.withLock { value } }
    /// Returns true only for the first caller.
    func setOnce() -> Bool {
        lock.withLock {
            if value { return false }
            value = true
            return true
        }
    }
}
