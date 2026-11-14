// Owner: Mac owner (H4). Microphone capture with AVAudioEngine, converted to 16 kHz mono Float for Whisper.
import AVFoundation
import Foundation

/// Records the default input device into an in-memory 16 kHz mono buffer between `start()` and `stop()`.
final class AudioCapture: @unchecked Sendable {
    static let sampleRate: Double = 16_000

    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private var samples: [Float] = []
    private let targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: AudioCapture.sampleRate,
                                             channels: 1, interleaved: false)!

    var isRunning: Bool { lock.withLock { engine != nil } }

    /// Seconds of audio captured so far.
    var capturedSeconds: Double { lock.withLock { Double(samples.count) / Self.sampleRate } }

    /// Snapshot of the samples captured so far.
    var currentSamples: [Float] { lock.withLock { samples } }

    /// Opens the microphone. Throws if the input device cannot be started (no permission, no device).
    func start() throws {
        stopEngine()
        lock.withLock { samples.removeAll(keepingCapacity: true) }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw HelperError(.permissionDenied, "no microphone input available")
        }
        guard let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            throw HelperError(.actionFailed, "cannot convert \(inputFormat) to 16 kHz mono")
        }
        // Downmix all channels to mono instead of only taking channel 0.
        converter.downmix = true

        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.append(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw HelperError(.permissionDenied, "microphone failed to start: \(error.localizedDescription)")
        }
        lock.withLock {
            self.engine = engine
            self.converter = converter
        }
    }

    /// Closes the microphone and returns everything captured.
    @discardableResult
    func stop() -> [Float] {
        stopEngine()
        return lock.withLock {
            let out = samples
            samples.removeAll()
            return out
        }
    }

    private func stopEngine() {
        let engine = lock.withLock { () -> AVAudioEngine? in
            let e = self.engine
            self.engine = nil
            self.converter = nil
            return e
        }
        guard let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        guard let converter = lock.withLock({ self.converter }) else { return }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, let channel = out.floatChannelData?[0], out.frameLength > 0 else { return }
        let chunk = UnsafeBufferPointer(start: channel, count: Int(out.frameLength))
        lock.withLock { samples.append(contentsOf: chunk) }
    }
}
