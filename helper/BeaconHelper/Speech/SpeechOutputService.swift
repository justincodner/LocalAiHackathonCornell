// Owner: Mac owner (H5). Speech output: one queue in front of TTSKit or the system voice (spec 4.3, 4.6, 9.5).
//
// BEACON_TTS=ttskit (default) loads Qwen3-TTS 0.6B from models/ttskit and falls back to AVSpeechSynthesizer if it
// fails to load, if its warm-up time to first audio exceeds 1 s, or if two sentences in a row take over 1 s to
// start. Until TTSKit has loaded, speech goes through the system voice so nothing is ever silent.
import Foundation

final class SpeechOutputService: SpeechOutputProviding, @unchecked Sendable {
    /// Time to first audio above which TTSKit is abandoned for the system voice (spec 4.3).
    static let maxTimeToFirstAudio = 1.0

    let events: EventHub
    private let system: SpeechEngine
    private let ttskit: TTSKitEngine?
    private let active: LockedValue<SpeechEngine>
    private let slowStreak = LockedValue(0)
    private var queue: SpeechQueue!

    init(events: EventHub, engine: String = Config.tts) {
        self.events = events
        let system = SystemSpeechEngine()
        self.system = system
        self.ttskit = engine.lowercased() == "system" ? nil : TTSKitEngine()
        self.active = LockedValue(system)
        let active = self.active
        queue = SpeechQueue(
            engineProvider: { active.get() },
            fallback: { [weak self] failed, error in
                guard let self, failed !== self.system else { return nil }
                self.fallBack("ttskit failed while speaking: \(error)")
                return self.system
            },
            emitStarted: { id, text in events.emit("tts.started", ["speech_id": id], localFields: ["text": text]) },
            emit: { type, fields in events.emit(type, fields) })
        ttskit?.onTimeToFirstAudio = { [weak self] seconds in self?.recordTimeToFirstAudio(seconds) }
    }

    /// For tests: a service around arbitrary engines.
    init(events: EventHub, queueEngine: SpeechEngine) {
        self.events = events
        self.system = queueEngine
        self.ttskit = nil
        self.active = LockedValue(queueEngine)
        let active = self.active
        queue = SpeechQueue(engineProvider: { active.get() }, emit: { type, fields in events.emit(type, fields) })
    }

    var engineName: String { active.get().name }

    var isSpeaking: Bool { queue.isSpeaking }

    func prewarm() async {
        try? await system.prepare()
        guard let ttskit else {
            Log.info("tts: using system voice (BEACON_TTS=system)")
            return
        }
        do {
            try await ttskit.prepare()
        } catch {
            Log.error("tts: ttskit failed to load (\(error)); falling back to the system voice")
            return
        }
        if ttskit.warmupFirstAudioSeconds > Self.maxTimeToFirstAudio {
            Log.warn(String(format: "tts: ttskit time to first audio %.0f ms exceeds %.0f ms; falling back to the system voice",
                            ttskit.warmupFirstAudioSeconds * 1000, Self.maxTimeToFirstAudio * 1000))
            return
        }
        active.set(ttskit)
        Log.info("tts: using ttskit (load \(Int(ttskit.loadSeconds * 1000)) ms, first audio \(Int(ttskit.warmupFirstAudioSeconds * 1000)) ms)")
    }

    func speak(text: String, interrupt: Bool, rate: Double?) -> String {
        queue.enqueue(text: text, interrupt: interrupt, rate: rate)
    }

    func stop() {
        queue.stop()
    }

    private func recordTimeToFirstAudio(_ seconds: Double) {
        guard seconds > Self.maxTimeToFirstAudio else {
            slowStreak.set(0)
            return
        }
        let streak = slowStreak.get() + 1
        slowStreak.set(streak)
        Log.warn(String(format: "tts: ttskit time to first audio %.0f ms", seconds * 1000))
        if streak >= 2 { fallBack("ttskit time to first audio over 1 s twice in a row") }
    }

    private func fallBack(_ reason: String) {
        guard active.get() !== system else { return }
        active.set(system)
        Log.warn("tts: falling back to the system voice: \(reason)")
    }
}
