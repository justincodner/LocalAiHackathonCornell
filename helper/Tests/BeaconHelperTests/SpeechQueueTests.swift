import XCTest
@testable import BeaconHelper

/// Engine that "plays" each sentence for a fixed time, honouring cancellation like a real player.
final class FakeSpeechEngine: SpeechEngine, @unchecked Sendable {
    let name: String
    let sentenceSeconds: Double
    let failing: Bool
    private let lock = NSLock()
    private(set) var spoken: [String] = []
    private(set) var rates: [Double] = []
    private(set) var stopAllCount = 0

    init(name: String = "fake", sentenceSeconds: Double = 0.2, failing: Bool = false) {
        self.name = name
        self.sentenceSeconds = sentenceSeconds
        self.failing = failing
    }

    func prepare() async throws {}

    func speak(_ sentence: String, rate: Double, token: SpeechCancelToken,
               onFirstAudio: @escaping @Sendable () -> Void) async throws {
        if failing { throw HelperError(.actionFailed, "fake failure") }
        lock.withLock { spoken.append(sentence); rates.append(rate) }
        onFirstAudio()
        let deadline = Date().addingTimeInterval(sentenceSeconds)
        while Date() < deadline {
            if token.isCancelled { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
    }

    func stopAll() { lock.withLock { stopAllCount += 1 } }

    var spokenSnapshot: [String] { lock.withLock { spoken } }
    var ratesSnapshot: [Double] { lock.withLock { rates } }
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
    private func makeQueue(_ engine: FakeSpeechEngine, fallback: FakeSpeechEngine? = nil)
        -> (SpeechQueue, EventRecorder) {
        let rec = EventRecorder()
        let queue = SpeechQueue(engineProvider: { engine },
                                fallback: { _, _ in fallback },
                                emit: { rec.record($0, $1) })
        return (queue, rec)
    }

    func testSpeaksSentenceBySentenceAndFinishes() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.02)
        let (queue, rec) = makeQueue(engine)
        let id = queue.enqueue(text: "One. Two! Three?", interrupt: false, rate: nil)
        XCTAssertEqual(id, "sp_0001")
        XCTAssertTrue(queue.isSpeaking)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertEqual(engine.spokenSnapshot, ["One.", "Two!", "Three?"])
        XCTAssertTrue(rec.started(id))
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
        XCTAssertFalse(queue.isSpeaking)
        // started precedes finished, and is emitted once.
        let types = rec.all.map(\.0)
        XCTAssertEqual(types, ["tts.started", "tts.finished"])
    }

    func testQueuesInOrder() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.03)
        let (queue, rec) = makeQueue(engine)
        let a = queue.enqueue(text: "First.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Second.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(b) != nil }
        XCTAssertEqual(engine.spokenSnapshot, ["First.", "Second."])
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, false)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, false)
    }

    func testInterruptFlushesQueueAndStopsCurrent() {
        let engine = FakeSpeechEngine(sentenceSeconds: 0.5)
        let (queue, rec) = makeQueue(engine)
        let a = queue.enqueue(text: "Long one. Still going.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Queued.", interrupt: false, rate: nil)
        rec.waitFor { rec.started(a) }
        let c = queue.enqueue(text: "Urgent.", interrupt: true, rate: nil)
        rec.waitFor { rec.finished(c) != nil }
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, true)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, true)
        XCTAssertFalse(rec.started(b))
        XCTAssertEqual(rec.finished(c)?["interrupted"] as? Bool, false)
        XCTAssertEqual(engine.spokenSnapshot, ["Long one.", "Urgent."])
    }

    func testStopSilencesWithin100msAndFlushes() {
        let engine = FakeSpeechEngine(sentenceSeconds: 2)
        let (queue, rec) = makeQueue(engine)
        let a = queue.enqueue(text: "Talking for a while.", interrupt: false, rate: nil)
        let b = queue.enqueue(text: "Never heard.", interrupt: false, rate: nil)
        rec.waitFor { rec.started(a) }
        let t0 = Date()
        queue.stop()
        rec.waitFor { rec.finished(a) != nil }
        XCTAssertLessThan(Date().timeIntervalSince(t0), 0.1)
        XCTAssertEqual(rec.finished(a)?["interrupted"] as? Bool, true)
        XCTAssertEqual(rec.finished(b)?["interrupted"] as? Bool, true)
        XCTAssertGreaterThanOrEqual(engine.stopAllCount, 1)
        rec.waitFor { !queue.isSpeaking }
        XCTAssertFalse(queue.isSpeaking)
        XCTAssertEqual(engine.spokenSnapshot, ["Talking for a while."])
    }

    func testStopWhenIdleIsHarmless() {
        let engine = FakeSpeechEngine()
        let (queue, rec) = makeQueue(engine)
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
        let (queue, rec) = makeQueue(engine)
        _ = queue.enqueue(text: "Slow.", interrupt: false, rate: 0.1)
        _ = queue.enqueue(text: "Normal.", interrupt: false, rate: nil)
        let last = queue.enqueue(text: "Fast.", interrupt: false, rate: 7)
        rec.waitFor { rec.finished(last) != nil }
        XCTAssertEqual(engine.ratesSnapshot, [0.5, 1.0, 1.5])
    }

    func testEmptyTextFinishesWithoutStarting() {
        let engine = FakeSpeechEngine()
        let (queue, rec) = makeQueue(engine)
        let id = queue.enqueue(text: "   ", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertFalse(rec.started(id))
        XCTAssertEqual(rec.finished(id)?["interrupted"] as? Bool, false)
    }

    func testFallsBackToOtherEngineOnFailure() {
        let broken = FakeSpeechEngine(name: "ttskit", failing: true)
        let system = FakeSpeechEngine(name: "system", sentenceSeconds: 0.01)
        let (queue, rec) = makeQueue(broken, fallback: system)
        let id = queue.enqueue(text: "Still heard.", interrupt: false, rate: nil)
        rec.waitFor { rec.finished(id) != nil }
        XCTAssertEqual(system.spokenSnapshot, ["Still heard."])
        XCTAssertTrue(rec.started(id))
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
