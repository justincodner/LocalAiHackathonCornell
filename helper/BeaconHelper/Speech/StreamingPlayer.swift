// Owner: Mac owner (H5). Low-latency PCM playback for streamed TTS audio, with a hard stop for barge-in.
import AVFoundation
import Foundation

/// An always-running AVAudioEngine with one player node and a time-pitch unit for rate changes.
/// One "utterance" at a time: `begin`, `append` chunks, `finishAndWait`, or `stopNow` from any thread.
final class StreamingPlayer: @unchecked Sendable {
    let sampleRate: Double
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let timePitch = AVAudioUnitTimePitch()
    private let format: AVAudioFormat

    private let lock = NSLock()
    private var pendingSamples: [Float] = []     // held back until the pre-buffer is full
    private var preBufferSamples = 0
    private var playing = false
    private var scheduled = 0
    private var completed = 0
    private var session = 0
    private var stopped = false
    private var onFirstAudio: (@Sendable () -> Void)?
    private var drainWaiter: CheckedContinuation<Void, Never>?

    init(sampleRate: Double) {
        self.sampleRate = sampleRate
        format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)!
        engine.attach(player)
        engine.attach(timePitch)
        engine.connect(player, to: timePitch, format: format)
        engine.connect(timePitch, to: engine.mainMixerNode, format: format)
    }

    func startEngine() throws {
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
    }

    /// Starts a new utterance. `preBuffer` seconds of audio are collected before playback starts.
    func begin(rate: Double, preBuffer: Double, onFirstAudio: @escaping @Sendable () -> Void) throws {
        try startEngine()
        lock.withLock {
            session += 1
            pendingSamples.removeAll(keepingCapacity: true)
            preBufferSamples = max(0, Int(preBuffer * sampleRate))
            playing = false
            scheduled = 0
            completed = 0
            stopped = false
            self.onFirstAudio = onFirstAudio
        }
        timePitch.rate = Float(rate)
        player.stop()
    }

    /// Raises the pre-buffer target (used once the first generation step has been timed).
    func setPreBuffer(seconds: Double) {
        lock.withLock { preBufferSamples = max(0, Int(seconds * sampleRate)) }
    }

    func append(_ samples: [Float]) {
        guard !samples.isEmpty else { return }
        let flushNow: [Float]? = lock.withLock {
            if stopped { return nil }
            pendingSamples.append(contentsOf: samples)
            if playing || pendingSamples.count >= preBufferSamples {
                let out = pendingSamples
                pendingSamples.removeAll(keepingCapacity: true)
                return out
            }
            return nil
        }
        if let flushNow { schedule(flushNow) }
    }

    /// Flushes any held-back audio and waits until everything scheduled has played (or `stopNow` was called).
    func finishAndWait() async {
        let rest: [Float] = lock.withLock {
            let out = pendingSamples
            pendingSamples.removeAll()
            return stopped ? [] : out
        }
        if !rest.isEmpty { schedule(rest) }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            let resumeNow = lock.withLock { () -> Bool in
                if stopped || completed >= scheduled { return true }
                drainWaiter = cont
                return false
            }
            if resumeNow { cont.resume() }
        }
    }

    /// Silences audio immediately: stops the player node, which drops every scheduled buffer.
    func stopNow() {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            stopped = true
            pendingSamples.removeAll()
            let w = drainWaiter
            drainWaiter = nil
            return w
        }
        player.stop()
        waiter?.resume()
    }

    private func schedule(_ samples: [Float]) {
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { src in
            buffer.floatChannelData![0].update(from: src.baseAddress!, count: samples.count)
        }
        let (mySession, firstAudio, startPlayer) = lock.withLock { () -> (Int, (@Sendable () -> Void)?, Bool) in
            scheduled += 1
            let start = !playing
            playing = true
            let cb = onFirstAudio
            onFirstAudio = nil
            return (session, cb, start)
        }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.bufferDone(session: mySession)
        }
        if startPlayer {
            player.play()
        }
        // The buffer is now on its way to the output device (one IO cycle, ~10 ms).
        firstAudio?()
    }

    private func bufferDone(session finishedSession: Int) {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            guard finishedSession == session else { return nil }
            completed += 1
            if completed >= scheduled, let w = drainWaiter {
                drainWaiter = nil
                return w
            }
            return nil
        }
        waiter?.resume()
    }
}
