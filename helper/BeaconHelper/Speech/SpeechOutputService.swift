// Owner: Mac owner (H5). Speech output: one queue that renders each utterance completely, then plays it (spec 4.3, 4.6, 9.5).
//
// Render-then-play: one /tts/speak request is one utterance. It is rendered to PCM in full before any of it plays,
// then played as one continuous buffer through ClipPlayer (48 kHz mono; every engine's audio is converted to it).
// Streaming playback crackled whenever generation fell behind real time, and TTSKit generates at only 0.4-0.8x
// real time on the 8 GB M1 with Bonsai loaded, so it always fell behind.
//
// Engine choice is made per utterance, before rendering, and never changes mid-way. Candidates, best first:
//   1. TTSKit (Qwen3-TTS 0.6B; only with BEACON_TTS=ttskit, since the default is `system`) once loaded,
//   2. the installed premium/enhanced en-US system voice, if any (measured at prewarm),
//   3. the compact Samantha system voice, which renders dozens of times faster than real time.
// The first candidate whose measured speed means the whole render should finish within
// `SpeechAudio.renderBudget` (1.5 s) is used; if it fails or exceeds `SpeechQueue.renderTimeout`, the next one
// renders the utterance instead, still before anything plays.
import AVFoundation
import Foundation

final class SpeechOutputService: SpeechOutputProviding, @unchecked Sendable {
    let events: EventHub
    private let ttskit: TTSKitEngine?
    private let natural: SystemSpeechEngine?
    private let compact: SpeechEngine
    private let player: ClipPlayer?
    private var queue: SpeechQueue!

    init(events: EventHub, engine: String = Config.tts) {
        self.events = events
        let naturalVoice = SystemSpeechEngine.naturalEnglishVoice()
        let compactVoice = SystemSpeechEngine.compactEnglishVoice()
        // Compact voices render at well over 10x real time; plan with that until prewarm measures it.
        let compact = SystemSpeechEngine(voice: compactVoice, assumedSpeed: 20)
        self.compact = compact
        natural = naturalVoice.flatMap { v in
            v.identifier == compactVoice?.identifier ? nil : SystemSpeechEngine(voice: v, assumedSpeed: 0)
        }
        ttskit = engine.lowercased() == "system" ? nil : TTSKitEngine()
        let player = ClipPlayer()
        self.player = player
        let candidates: [SpeechEngine] = ([ttskit, natural] as [SpeechEngine?]).compactMap { $0 } + [compact]
        queue = SpeechQueue(
            engines: { item in Self.plan(candidates, for: item) },
            player: player,
            emitStarted: { id, text in events.emit("tts.started", ["speech_id": id], localFields: ["text": text]) },
            emit: { type, fields in events.emit(type, fields) })
    }

    /// For tests: a service around arbitrary engines and an output.
    init(events: EventHub, queueEngine: SpeechEngine, output: AudioOutput) {
        self.events = events
        ttskit = nil
        natural = nil
        compact = queueEngine
        player = nil
        queue = SpeechQueue(engines: { _ in [queueEngine] }, player: output,
                            emit: { type, fields in events.emit(type, fields) })
    }

    /// The engines to try for `item`: the planner's choice, then every later candidate as a fallback.
    static func plan(_ candidates: [SpeechEngine], for item: SpeechQueue.Item) -> [SpeechEngine] {
        let index = SpeechAudio.chooseEngine(
            candidates.map { SpeechAudio.Candidate(speedFactor: $0.speedFactor, rendersRate: $0.rendersRate) },
            characters: item.sentences.reduce(0) { $0 + $1.count }, sentences: item.sentences.count, rate: item.rate)
        return Array(candidates[index...])
    }

    var engineName: String { ttskit?.isLoaded == true ? "ttskit" : "system" }

    var isSpeaking: Bool { queue.isSpeaking }

    func prewarm() async {
        do { try player?.start() } catch { Log.error("tts: audio output failed to start: \(error)") }
        try? await compact.prepare()
        try? await natural?.prepare()
        guard let ttskit else {
            Log.info("tts: using the system voice (BEACON_TTS=system)")
            return
        }
        do {
            try await ttskit.prepare()
        } catch {
            Log.error("tts: ttskit failed to load (\(error)); using the system voice")
            return
        }
        let budgetChars = Int(SpeechAudio.renderBudget * ttskit.speedFactor / SpeechAudio.secondsPerCharacter)
        Log.info("tts: ttskit ready (load \(Int(ttskit.loadSeconds * 1000)) ms, "
                 + String(format: "%.2fx real time", ttskit.speedFactor)
                 + "); used for utterances it can render within \(SpeechAudio.renderBudget) s (about \(budgetChars) characters)")
    }

    func speak(text: String, interrupt: Bool, rate: Double?) -> String {
        queue.enqueue(text: text, interrupt: interrupt, rate: rate)
    }

    func stop() {
        queue.stop()
    }
}
