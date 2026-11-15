// Owner: Mac owner (H5). The single speech queue: render a whole utterance, then play it in one piece.
//
// - `enqueue(interrupt: false)` appends; `interrupt: true` flushes queued items and stops the current one first.
// - `stop()` flushes everything, cancels any render in flight and silences the player immediately.
// - Each item is rendered completely (by the first engine the planner picks; on failure or timeout by the next one)
//   before any of it plays, then played as one buffer. No streaming, no engine switch mid-utterance.
// - Every item emits `tts.started {speech_id}` when its audio starts (if it got that far) and exactly one
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

    /// Longest a render may take before the next engine is tried; the last engine gets `finalRenderTimeout`.
    static let renderTimeout = 3.0
    static let finalRenderTimeout = 20.0

    private let lock = NSLock()
    private var pending: [Item] = []
    private var current: Item?
    private var workerRunning = false
    private let ids: SequentialID
    private let engines: @Sendable (Item) -> [SpeechEngine]
    private let player: AudioOutput
    private let emit: Emit
    private let emitStarted: @Sendable (String, String) -> Void
    private let renderTimeout: Double

    /// - Parameters:
    ///   - engines: the engines to try for an item, best first; later ones are fallbacks if a render fails or times out.
    ///   - player: plays each rendered utterance.
    init(ids: SequentialID = SequentialID(prefix: "sp"),
         engines: @escaping @Sendable (Item) -> [SpeechEngine],
         player: AudioOutput,
         renderTimeout: Double = SpeechQueue.renderTimeout,
         emitStarted: (@Sendable (String, String) -> Void)? = nil,
         emit: @escaping Emit) {
        self.ids = ids
        self.engines = engines
        self.player = player
        self.renderTimeout = renderTimeout
        self.emit = emit
        self.emitStarted = emitStarted ?? { id, _ in emit("tts.started", ["speech_id": id]) }
    }

    /// True while an item is being rendered or played, or items are waiting.
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
        // Belt and braces: silence the player even if the item has not wired its token yet.
        if active != nil { player.stop() }
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
            await speak(item)
            lock.withLock { current = nil }
            emit("tts.finished", ["speech_id": item.id, "interrupted": item.token.isCancelled])
        }
    }

    private func speak(_ item: Item) async {
        guard !item.sentences.isEmpty,
              let (audio, engine) = await render(item), !audio.isEmpty, !item.token.isCancelled
        else { return }
        let rate = engine.rendersRate ? 1 : item.rate
        let emitStarted = self.emitStarted
        let started = StartedFlag()
        await player.play(audio, rate: rate, token: item.token) {
            if started.setOnce() { emitStarted(item.id, item.text) }
        }
    }

    /// The whole utterance as one limited, faded buffer, from the first engine that renders it in time.
    private func render(_ item: Item) async -> ([Float], SpeechEngine)? {
        let candidates = engines(item)
        for (index, engine) in candidates.enumerated() {
            if item.token.isCancelled { return nil }
            let timeout = index == candidates.count - 1 ? max(renderTimeout, Self.finalRenderTimeout) : renderTimeout
            let attempt = SpeechCancelToken()
            item.token.onCancel { attempt.cancel() }
            let timedOut = StartedFlag()
            let timer = Task {
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                if !Task.isCancelled, timedOut.setOnce() { attempt.cancel() }
            }
            let start = CFAbsoluteTimeGetCurrent()
            do {
                let clips = try await engine.render(item.sentences, rate: item.rate, token: attempt)
                timer.cancel()
                if item.token.isCancelled { return nil }
                if timedOut.isSet { throw SpeechRenderTimeout(seconds: timeout) }
                let audio = SpeechAudio.softLimited(SpeechAudio.assemble(clips))
                if audio.isEmpty { throw SpeechRenderEmpty() }
                Log.info(String(format: "tts: %@ rendered %.2fs of audio in %.2fs", engine.name,
                                Double(audio.count) / SpeechAudio.sampleRate, CFAbsoluteTimeGetCurrent() - start))
                return (audio, engine)
            } catch {
                timer.cancel()
                if item.token.isCancelled { return nil }
                let reason: Error = timedOut.isSet ? SpeechRenderTimeout(seconds: timeout) : error
                Log.warn("tts: \(engine.name) could not render the utterance (\(reason))"
                         + (index + 1 < candidates.count ? "; using \(candidates[index + 1].name)" : ""))
            }
        }
        return nil
    }
}

struct SpeechRenderTimeout: Error, CustomStringConvertible {
    let seconds: Double
    var description: String { String(format: "render took over %.1f s", seconds) }
}

struct SpeechRenderEmpty: Error, CustomStringConvertible {
    var description: String { "render produced no audio" }
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
