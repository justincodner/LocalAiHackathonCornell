// Owner: Mac owner (H5). AVSpeechSynthesizer engine: offline, and the fallback for TTSKit (spec 4.3).
// It renders to buffers with `write(_:toBufferCallback:)`; ClipPlayer plays the result like any other engine's.
import AVFoundation
import Foundation

final class SystemSpeechEngine: NSObject, SpeechEngine, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    let name = "system"
    let rendersRate = true
    let voice: AVSpeechSynthesisVoice?
    private let synthesizer = AVSpeechSynthesizer()
    private let lock = NSLock()
    private var active: (utterance: AVSpeechUtterance, cont: CheckedContinuation<Void, Never>)?
    private let speed: LockedValue<Double>

    /// - Parameters:
    ///   - voice: the voice to render with.
    ///   - assumedSpeed: render speed to plan with before one is measured (0 = unknown until `prepare` measures it).
    init(voice: AVSpeechSynthesisVoice?, assumedSpeed: Double) {
        self.voice = voice
        speed = LockedValue(assumedSpeed)
        super.init()
        synthesizer.delegate = self
    }

    var speedFactor: Double { speed.get() }

    /// Maps the contract's 0.5...1.5 rate onto AVSpeechUtterance's scale, where 0.5 is the normal rate.
    static func utteranceRate(for rate: Double) -> Float {
        let r = Float(SpeechRate.clamp(rate))
        let mapped = AVSpeechUtteranceDefaultSpeechRate * r
        return min(max(mapped, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)
    }

    /// A premium or enhanced en-US voice if one is installed (often a Siri "natural" voice, which is a neural model
    /// and renders at only a few times real time), else nil.
    static func naturalEnglishVoice() -> AVSpeechSynthesisVoice? {
        let english = AVSpeechSynthesisVoice.speechVoices().filter { $0.language == "en-US" }
        return english.first(where: { $0.quality == .premium }) ?? english.first(where: { $0.quality == .enhanced })
    }

    /// The compact Samantha voice: renders dozens of times faster than real time, so it is always the last resort.
    static func compactEnglishVoice() -> AVSpeechSynthesisVoice? {
        AVSpeechSynthesisVoice(identifier: "com.apple.voice.compact.en-US.Samantha")
            ?? AVSpeechSynthesisVoice.speechVoices().first { $0.language == "en-US" && $0.quality == .default
                && $0.identifier.hasPrefix("com.apple.voice.") }
            ?? AVSpeechSynthesisVoice(language: "en-US")
    }

    /// Renders a short phrase twice (not played): once to load the voice (seconds when cold), once to measure its speed.
    func prepare() async throws {
        let token = SpeechCancelToken()
        _ = try await render(["Okay."], rate: 1, token: token)
        let start = CFAbsoluteTimeGetCurrent()
        let clips = try await render(["Mail, inbox. Eleven more messages."], rate: 1, token: token)
        let audio = Double(clips.reduce(0) { $0 + $1.count }) / SpeechAudio.sampleRate
        let wall = CFAbsoluteTimeGetCurrent() - start
        if audio > 0.3 {
            speed.set(audio / max(wall, 0.001))
        }
        Log.info(String(format: "tts: system voice %@ (%@) warm-up %.2fs of audio in %.2fs",
                        voice?.name ?? "default", voice?.identifier ?? "-", audio, wall))
    }

    func render(_ sentences: [String], rate: Double, token: SpeechCancelToken) async throws -> [[Float]] {
        let text = sentences.joined(separator: " ")
        guard !text.isEmpty, !token.isCancelled else { return [] }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = voice
        utterance.rate = Self.utteranceRate(for: rate)
        utterance.preUtteranceDelay = 0
        utterance.postUtteranceDelay = 0

        let collected = SampleSink()
        let sourceRate = LockedValue<Double>(0)
        let start = CFAbsoluteTimeGetCurrent()
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            lock.withLock { active = (utterance, cont) }
            token.onCancel { [weak self] in self?.cancel(utterance) }
            if token.isCancelled { return }   // onCancel already cancelled and resumed
            synthesizer.write(utterance) { [weak self] buffer in
                guard let pcm = buffer as? AVAudioPCMBuffer else { return }
                if pcm.frameLength == 0 {     // end of the utterance
                    self?.finish(utterance)
                    return
                }
                let samples = SpeechAudio.monoFloat(pcm)
                sourceRate.set(pcm.format.sampleRate)
                collected.append(samples)
            }
        }
        if token.isCancelled { return [] }
        let samples = SpeechAudio.resample(collected.samples, from: sourceRate.get())
        let audioSeconds = Double(samples.count) / SpeechAudio.sampleRate
        let wall = CFAbsoluteTimeGetCurrent() - start
        if audioSeconds > 0.5, wall > 0 { speed.set(0.7 * speed.get() + 0.3 * audioSeconds / wall) }
        return [samples]
    }

    private func cancel(_ utterance: AVSpeechUtterance) {
        let mine = lock.withLock { active?.utterance === utterance }
        if mine { synthesizer.stopSpeaking(at: .immediate) }
        finish(utterance)
    }

    // MARK: AVSpeechSynthesizerDelegate

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        finish(utterance)
    }

    /// Backup for systems that never send the empty end-of-utterance buffer; delayed so late buffers still land.
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { [weak self] in self?.finish(utterance) }
    }

    private func finish(_ utterance: AVSpeechUtterance) {
        let cont = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            guard let a = active, a.utterance === utterance else { return nil }
            active = nil
            return a.cont
        }
        cont?.resume()
    }
}

/// Lock-protected sample accumulator for synthesizer callbacks.
final class SampleSink: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer: [Float] = []
    func append(_ s: [Float]) { lock.withLock { buffer.append(contentsOf: s) } }
    var samples: [Float] { lock.withLock { buffer } }
}
