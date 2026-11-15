import XCTest
@testable import BeaconHelper

/// Engine that "renders" each sentence as a clip of `sentenceSeconds` of audio after `renderSeconds`,
/// honouring cancellation like a real engine.
final class FakeSpeechEngine: SpeechEngine, @unchecked Sendable {
    let name: String
    let sentenceSeconds: Double
    let renderSeconds: Double
    let failing: Bool
    let rendersRate = true
    var speedFactor: Double { 10 }
    private let lock = NSLock()
    private(set) var spoken: [String] = []
    private(set) var rates: [Double] = []

    init(name: String = "fake", sentenceSeconds: Double = 0.2, renderSeconds: Double = 0, failing: Bool = false) {
        self.name = name
        self.sentenceSeconds = sentenceSeconds
        self.renderSeconds = renderSeconds
        self.failing = failing
    }

    func prepare() async throws {}

    func render(_ sentences: [String], rate: Double, token: SpeechCancelToken) async throws -> [[Float]] {
        if failing { throw HelperError(.actionFailed, "fake failure") }
        let deadline = Date().addingTimeInterval(renderSeconds)
        while Date() < deadline {
            if token.isCancelled { return [] }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        lock.withLock { spoken.append(contentsOf: sentences); rates.append(rate) }
        return sentences.map { _ in [Float](repeating: 0.1, count: Int(sentenceSeconds * SpeechAudio.sampleRate)) }
    }

    var spokenSnapshot: [String] { lock.withLock { spoken } }
    var ratesSnapshot: [Double] { lock.withLock { rates } }
}

/// Output that "plays" a clip for its real duration, honouring cancellation like ClipPlayer.
final class FakeAudioOutput: AudioOutput, @unchecked Sendable {
    private let lock = NSLock()
    private var played: [(samples: Int, rate: Double)] = []
    private var stopCount = 0

    func play(_ samples: [Float], rate: Double, token: SpeechCancelToken, onStart: @escaping @Sendable () -> Void) async {
        lock.withLock { played.append((samples.count, rate)) }
        onStart()
        let deadline = Date().addingTimeInterval(Double(samples.count) / SpeechAudio.sampleRate / rate)
        while Date() < deadline {
            if token.isCancelled { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
    }

    func stop() { lock.withLock { stopCount += 1 } }

    var playedSnapshot: [(samples: Int, rate: Double)] { lock.withLock { played } }
    var stopCountSnapshot: Int { lock.withLock { stopCount } }
}

/// Collects emitted events in order.
final class EventRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [(String, JSONObject)] = []

    func record(_ type: String, _ fields: JSONObject) { lock.withLock { events.append((type, fields)) } }

    var all: [(String, JSONObject)] { lock.withLock { events } }

    func finished(_ id: String) -> JSONObject? {
        all.first { $0.0 == "tts.finished" && $0.1["speech_id"] as? String == id }?.1
    }

    func started(_ id: String) -> Bool {
        all.contains { $0.0 == "tts.started" && $0.1["speech_id"] as? String == id }
    }

    func waitFor(timeout: Double = 3, _ condition: () -> Bool) {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() && Date() < deadline { usleep(5_000) }
    }
}

final class SpeechQueueTests: XCTestCase {
    private func makeQueue(_ engines: [FakeSpeechEngine], renderTimeout: Double = SpeechQueue.renderTimeout)
        -> (SpeechQueue, EventRecorder, FakeAudioOutput) {
        let rec = EventRecorder()
        let output = FakeAudioOutput()
        let queue = SpeechQueue(engines: { _ in engines }, player: output, renderTimeout: renderTimeout,
                                emit: { rec.record($0, $1) })
        return (queue, rec, output)
    }

    private func makeQueue(_ engine: FakeSpeechEngine) -> (SpeechQueue, EventRecorder, FakeAudioOutput) {
        makeQueue([engine])
    }

    func testRendersWholeUtteranceThenPlaysItAsOneClip() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.02)
        let (queue, rec, output) = makeQueue(engine)
        let id = queue.enqueue(text: "One. Two! Three?", interrupt: false, rate: nil)
        XCTAssertEqual(id, "sp_0001")
        XCTAssertTrue(queue.isSpeaking)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertEqual(engine.spokenSnapshot, ["One.", "Two!", "Three?"])
        // One play call: three 20 ms clips plus two sentence pauses.
        let clip = Int(0.02 * SpeechAudio.sampleRate), pause = Int(SpeechAudio.sentencePause * SpeechAudio.sampleRate)
        XCTAssertEqual(output.playedSnapshot.map(\.samples), [3 * clip + 2 * pause])
        XCTAssertTrue(rec.started(id))
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
        XCTAssertFalse(queue.isSpeaking)
        // started precedes finished, and is emitted once.
        XCTAssertEqual(rec.all.map(\.0), ["tts.started", "tts.finished"])
    }

    func testStartedOnlyWhenAudioStartsNotWhileRendering() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.02, renderSeconds: 0.3)
        let (queue, rec, _) = makeQueue(engine)
        let id = queue.enqueue(text: "Takes a moment.", interrupt: false, rate: nil)
        usleep(150_000)
        XCTAssertFalse(rec.started(id), "nothing plays until the render is complete")
        XCTAssertTrue(queue.isSpeaking)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertTrue(rec.started(id))
    }

    func testQueuesInOrder() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.03)
        let (queue, rec, _) = makeQueue(engine)
        let a = queue.enqueue(text: "First.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Second.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(b) != nil }
        XCTAssertEqual(engine.spokenSnapshot, ["First.", "Second."])
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, false)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, false)
    }

    func testInterruptFlushesQueueAndStopsCurrent() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.5)
        let (queue, rec, _) = makeQueue(engine)
        let a = queue.enqueue(text: "Long one. Still going.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Queued.", interrupt: false, rate: nil)
        rec.waitFor { rec.started(a) }
        let c = queue.enqueue(text: "Urgent.", interrupt: true, rate: nil)
        rec.waitFor { rec.finished(c) != nil }
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, true)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, true)
        XCTAssertFalse(rec.started(b))
        XCTAssertEqual(rec.finished(c)?["interrupted"] as? Bool, false)
        XCTAssertEqual(engine.spokenSnapshot, ["Long one.", "Still going.", "Urgent."])
    }

    func testStopSilencesWithin100msAndFlushes() {
        let engine = FakeSpeechEngine(sentenceSeconds: 2)
        let (queue, rec, output) = makeQueue(engine)
        let a = queue.enqueue(text: "Talking for a while.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Never heard.", interrupt: false, rate: nil)
        rec.waitFor { rec.started(a) }
        let t0 = Date()
        queue.stop()
        rec.waitFor { rec.finished(a) != nil }
        XCTAssertLessThan(Date().timeIntervalSince(t0), 0.1)
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, true)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, true)
        XCTAssertGreaterThanOrEqual(output.stopCountSnapshot, 1)
        rec.waitFor { !queue.isSpeaking }
        XCTAssertFalse(queue.isSpeaking)
        XCTAssertEqual(engine.spokenSnapshot, ["Talking for a while."])
    }

    func testStopDuringRenderFinishesWithin100msWithoutAudio() {
        let engine = FakeSpeechEngine(sentenceSeconds: 1, renderSeconds: 2)
        let (queue, rec, output) = makeQueue(engine)
        let a = queue.enqueue(text: "Still rendering.", interrupt: false, rate: nil)
        usleep(100_000)
        let t0 = Date()
        queue.stop()
        rec.waitFor { rec.finished(a) != nil }
        XCTAssertLessThan(Date().timeIntervalSince(t0), 0.1)
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, true)
        XCTAssertFalse(rec.started(a))
        XCTAssertTrue(output.playedSnapshot.isEmpty)
    }

    func testStopWhenIdleIsHarmless() {
        let engine = FakeSpeechEngine()
        let (queue, rec, _) = makeQueue(engine)
        queue.stop()
        XCTAssertFalse(queue.isSpeaking)
        XCTAssertTrue(rec.all.isEmpty)
        // Still usable afterwards.
        let id = queue.enqueue(text: "Hello.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
    }

    func testRateIsClampedPerItem() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.01)
        let (queue, rec, output) = makeQueue(engine)
        _ = queue.enqueue(text: "Slow.", interrupt: false, rate: 0.1)
        _ = queue.enqueue(text: "Normal.", interrupt: false, rate: nil)
        let last = queue.enqueue(text: "Fast.", interrupt: false, rate: 7)
        rec.waitFor { rec.finished(last) != nil }
        XCTAssertEqual(engine.ratesSnapshot, [0.5, 1.0, 1.5])
        // The fake renders at the requested rate, so the player never time-stretches.
        XCTAssertEqual(output.playedSnapshot.map(\.rate), [1, 1, 1])
    }

    func testEmptyTextFinishesWithoutStarting() {
        let engine = FakeSpeechEngine()
        let (queue, rec, _) = makeQueue(engine)
        let id = queue.enqueue(text: "   ", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertFalse(rec.started(id))
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
    }

    func testFallsBackToNextEngineOnFailureBeforePlaying() {
        let broken = FakeSpeechEngine(name: "ttskit", failing: true)
        let system = FakeSpeechEngine(name: "system", sentenceSeconds: 0.01)
        let (queue, rec, output) = makeQueue([broken, system])
        let id = queue.enqueue(text: "Still heard. Twice.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertEqual(system.spokenSnapshot, ["Still heard.", "Twice."])
        XCTAssertEqual(output.playedSnapshot.count, 1)
        XCTAssertTrue(rec.started(id))
    }

    func testFallsBackWhenRenderExceedsTimeout() {
        let slow = FakeSpeechEngine(name: "ttskit", sentenceSeconds: 0.01, renderSeconds: 5)
        let system = FakeSpeechEngine(name: "system", sentenceSeconds: 0.01)
        let (queue, rec, output) = makeQueue([slow, system], renderTimeout: 0.2)
        let t0 = Date()
        let id = queue.enqueue(text: "Too slow for TTSKit.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertLessThan(Date().timeIntervalSince(t0), 1)
        XCTAssertTrue(slow.spokenSnapshot.isEmpty)
        XCTAssertEqual(system.spokenSnapshot, ["Too slow for TTSKit."])
        XCTAssertEqual(output.playedSnapshot.count, 1)
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
    }

    func testCancelTokenRunsLateHandlersImmediately() {
        let token = SpeechCancelToken()
        var calls = 0
        token.onCancel { calls += 1 }
        token.cancel()
        token.cancel()
        token.onCancel { calls += 10 }
        XCTAssertEqual(calls, 11)
        XCTAssertTrue(token.isCancelled)
    }
}
