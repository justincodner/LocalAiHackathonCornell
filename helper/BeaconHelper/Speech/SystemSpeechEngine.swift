// Owner: Mac owner (H5). AVSpeechSynthesizer engine: offline, instant, and the fallback for TTSKit (spec 4.3).
import AVFoundation
import Foundation

final class SystemSpeechEngine: NSObject, SpeechEngine, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    let name = "system"
    private let synthesizer = AVSpeechSynthesizer()
    private let voice: AVSpeechSynthesisVoice?
    private let lock = NSLock()
    private var active: (utterance: AVSpeechUtterance, cont: CheckedContinuation<Void, Never>, onStart: @Sendable () -> Void)?

    override init() {
        voice = Self.bestEnglishVoice()
        super.init()
        synthesizer.delegate = self
    }

    /// Maps the contract's 0.5...1.5 rate onto AVSpeechUtterance's scale, where 0.5 is the normal rate.
    static func utteranceRate(for rate: Double) -> Float {
        let r = Float(SpeechRate.clamp(rate))
        let mapped = AVSpeechUtteranceDefaultSpeechRate * r
        return min(max(mapped, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)
    }

    /// Prefers a premium or enhanced en-US voice if one is installed, else the default en-US voice.
    static func bestEnglishVoice() -> AVSpeechSynthesisVoice? {
        let english = AVSpeechSynthesisVoice.speechVoices().filter { $0.language == "en-US" }
        if let premium = english.first(where: { $0.quality == .premium }) { return premium }
        if let enhanced = english.first(where: { $0.quality == .enhanced }) { return enhanced }
        return AVSpeechSynthesisVoice(language: "en-US")
    }

    func prepare() async throws {
        Log.info("tts: system voice \(voice?.name ?? "default") (\(voice?.identifier ?? "-"))")
    }

    func speak(_ sentence: String, rate: Double, token: SpeechCancelToken,
               onFirstAudio: @escaping @Sendable () -> Void) async throws {
        if token.isCancelled { return }
        let utterance = AVSpeechUtterance(string: sentence)
        utterance.voice = voice
        utterance.rate = Self.utteranceRate(for: rate)
        utterance.preUtteranceDelay = 0
        utterance.postUtteranceDelay = 0
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            lock.withLock { active = (utterance, cont, onFirstAudio) }
            token.onCancel { [weak self] in self?.stopAll() }
            if token.isCancelled { return }   // onCancel already stopped and resumed
            synthesizer.speak(utterance)
        }
    }

    func stopAll() {
        let pending = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            let c = active?.cont
            active = nil
            return c
        }
        synthesizer.stopSpeaking(at: .immediate)
        pending?.resume()
    }

    // MARK: AVSpeechSynthesizerDelegate

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        let onStart = lock.withLock { active?.utterance === utterance ? active?.onStart : nil }
        onStart?()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        finish(utterance)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        finish(utterance)
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
