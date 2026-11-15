// Owner: Mac owner (H5). Pure PCM helpers for render-then-play speech: format conversion, assembly, limiting, engine choice.
import AVFoundation
import Foundation

enum SpeechAudio {
    /// The one format the player uses: mono Float32 at 48 kHz. Every engine's audio is converted to it.
    static let sampleRate: Double = 48_000
    /// Silence between sentences of one utterance.
    static let sentencePause = 0.15
    /// Fade at each clip edge so a sentence never starts or stops on a non-zero sample (a click).
    static let edgeFade = 0.005
    /// Longest wait for rendering before playback starts that the planner accepts for a non-fallback engine.
    static let renderBudget = 1.5
    /// Typical speech length per character at normal rate.
    static let secondsPerCharacter = 0.075
    /// Fixed cost per sentence (prefill, decoder warm-up).
    static let overheadPerSentence = 0.1

    // MARK: Format conversion

    /// Mono Float32 samples from any PCM buffer (Float32, Int16 or Int32; channels are averaged).
    static func monoFloat(_ buffer: AVAudioPCMBuffer) -> [Float] {
        let frames = Int(buffer.frameLength)
        let channels = Int(buffer.format.channelCount)
        guard frames > 0, channels > 0 else { return [] }
        let interleaved = buffer.format.isInterleaved
        var out = [Float](repeating: 0, count: frames)
        func mix(_ read: (_ channel: Int, _ frame: Int) -> Float) {
            for f in 0..<frames {
                var sum: Float = 0
                for c in 0..<channels { sum += read(c, f) }
                out[f] = sum / Float(channels)
            }
        }
        if let data = buffer.floatChannelData {
            mix { c, f in interleaved ? data[0][f * channels + c] : data[c][f] }
        } else if let data = buffer.int16ChannelData {
            mix { c, f in Float(interleaved ? data[0][f * channels + c] : data[c][f]) / 32_768 }
        } else if let data = buffer.int32ChannelData {
            mix { c, f in Float(interleaved ? data[0][f * channels + c] : data[c][f]) / 2_147_483_648 }
        }
        return out
    }

    /// Resamples mono Float32 audio with AVAudioConverter (highest quality). Returns the input when the rates match.
    static func resample(_ samples: [Float], from sourceRate: Double, to targetRate: Double = sampleRate) -> [Float] {
        guard !samples.isEmpty, sourceRate > 0, abs(sourceRate - targetRate) > 0.5 else { return samples }
        guard let inFormat = AVAudioFormat(standardFormatWithSampleRate: sourceRate, channels: 1),
              let outFormat = AVAudioFormat(standardFormatWithSampleRate: targetRate, channels: 1),
              let converter = AVAudioConverter(from: inFormat, to: outFormat),
              let input = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: AVAudioFrameCount(samples.count))
        else { return samples }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        input.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { input.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }

        let expected = Int((Double(samples.count) * targetRate / sourceRate).rounded())
        var out: [Float] = []
        out.reserveCapacity(expected + 64)
        var fed = false
        while true {
            guard let chunk = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: AVAudioFrameCount(max(4096, expected + 1024)))
            else { break }
            var error: NSError?
            let status = converter.convert(to: chunk, error: &error) { _, inputStatus in
                if fed {
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                fed = true
                inputStatus.pointee = .haveData
                return input
            }
            if chunk.frameLength > 0 {
                out.append(contentsOf: UnsafeBufferPointer(start: chunk.floatChannelData![0], count: Int(chunk.frameLength)))
            }
            if status == .error {
                Log.warn("tts: resample \(Int(sourceRate)) -> \(Int(targetRate)) Hz failed: \(error.map { "\($0)" } ?? "?")")
                return out.isEmpty ? samples : out
            }
            if status == .endOfStream || chunk.frameLength == 0 { break }
        }
        return out
    }

    // MARK: Assembly

    /// Joins clips into one utterance: each clip trimmed of leading and trailing near-silence (TTSKit ends every
    /// sentence with ~0.5 s of it), faded in and out, and separated by `pause` seconds of silence.
    static func assemble(_ clips: [[Float]], sampleRate: Double = sampleRate,
                         pause: Double = sentencePause, fade: Double = edgeFade) -> [Float] {
        let parts = clips.map { trimmedSilence($0, sampleRate: sampleRate) }.filter { !$0.isEmpty }
        let gap = [Float](repeating: 0, count: Int(pause * sampleRate))
        var out: [Float] = []
        out.reserveCapacity(parts.reduce(0) { $0 + $1.count } + gap.count * max(0, parts.count - 1))
        for (i, clip) in parts.enumerated() {
            if i > 0 { out.append(contentsOf: gap) }
            out.append(contentsOf: faded(clip, samples: Int(fade * sampleRate)))
        }
        return out
    }

    /// Drops near-silence (below `threshold`) from both ends, keeping `keep` seconds of it next to the speech.
    /// A clip that is silent throughout comes back empty.
    static func trimmedSilence(_ clip: [Float], sampleRate: Double = sampleRate,
                               threshold: Float = 0.003, keep: Double = 0.03) -> [Float] {
        guard let first = clip.firstIndex(where: { abs($0) > threshold }),
              let last = clip.lastIndex(where: { abs($0) > threshold }) else { return [] }
        let pad = Int(keep * sampleRate)
        return Array(clip[max(0, first - pad)...min(clip.count - 1, last + pad)])
    }

    /// Linear fade-in and fade-out over `samples` samples at each end.
    static func faded(_ clip: [Float], samples: Int) -> [Float] {
        let n = min(samples, clip.count / 2)
        guard n > 0 else { return clip }
        var out = clip
        for i in 0..<n {
            let g = Float(i) / Float(n)
            out[i] *= g
            out[clip.count - 1 - i] *= g
        }
        return out
    }

    /// Keeps peaks under full scale with a soft knee so loud syllables never hard-clip into static.
    static func softLimited(_ samples: [Float], knee: Float = 0.8) -> [Float] {
        guard samples.contains(where: { abs($0) > knee }) else { return samples }
        let headroom = 1 - knee
        return samples.map { x in
            let a = abs(x)
            guard a > knee else { return x }
            let over = (a - knee) / headroom
            return (knee + headroom * Float(tanh(Double(over)))) * (x < 0 ? -1 : 1)
        }
    }

    // MARK: Engine choice

    struct Candidate: Equatable {
        /// Audio seconds rendered per wall-clock second; 0 means not ready.
        let speedFactor: Double
        /// True when the engine renders at the requested rate (so faster speech means less audio to render).
        let rendersRate: Bool
    }

    static func estimatedRenderSeconds(characters: Int, sentences: Int, rate: Double, candidate: Candidate) -> Double {
        guard candidate.speedFactor > 0 else { return .infinity }
        let audio = max(0.3, Double(characters) * secondsPerCharacter / (candidate.rendersRate ? max(rate, 0.1) : 1))
        return audio / candidate.speedFactor + overheadPerSentence * Double(max(1, sentences))
    }

    /// Index of the first candidate expected to render the whole utterance within `budget`; the last candidate
    /// (the instant compact system voice) when none is.
    static func chooseEngine(_ candidates: [Candidate], characters: Int, sentences: Int, rate: Double,
                             budget: Double = renderBudget) -> Int {
        guard !candidates.isEmpty else { return 0 }
        for (i, c) in candidates.enumerated().dropLast()
        where estimatedRenderSeconds(characters: characters, sentences: sentences, rate: rate, candidate: c) <= budget {
            return i
        }
        return candidates.count - 1
    }
}
