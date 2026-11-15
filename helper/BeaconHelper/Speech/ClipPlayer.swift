// Owner: Mac owner (H5). Plays one fully rendered utterance as a single buffer, with a hard stop for barge-in.
import AVFoundation
import Foundation

/// Where the queue sends a finished utterance. `ClipPlayer` in the app, a fake in tests.
protocol AudioOutput: AnyObject, Sendable {
    /// Plays mono Float32 `samples` at `SpeechAudio.sampleRate`, time-stretched to `rate` when it is not 1.
    /// Calls `onStart` once, as the audio starts. Returns when playback ends, `token` is cancelled, or `stop()` runs.
    func play(_ samples: [Float], rate: Double, token: SpeechCancelToken, onStart: @escaping @Sendable () -> Void) async
    /// Silences audio immediately.
    func stop()
}

/// An AVAudioEngine with one player node in one fixed format (48 kHz mono Float32). The player feeds the main
/// mixer directly; the time-pitch unit is wired in only while a non-1.0 rate is being played (it smears normal speech).
final class ClipPlayer: AudioOutput, @unchecked Sendable {
    static let format = AVAudioFormat(standardFormatWithSampleRate: SpeechAudio.sampleRate, channels: 1)!

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let timePitch = AVAudioUnitTimePitch()
    /// Serialises scheduling against `stop()`, so a stop can never slip between "not cancelled" and `play()`.
    private let playLock = NSLock()
    private let lock = NSLock()
    private var stretching = false
    private var session = 0
    private var waiter: CheckedContinuation<Void, Never>?
    private var observer: NSObjectProtocol?

    init() {
        engine.attach(player)
        engine.attach(timePitch)
        engine.connect(player, to: engine.mainMixerNode, format: Self.format)
        // The output device changed (headphones, AirPods): the engine has stopped. End the current clip so the
        // queue never hangs; the next clip restarts the engine.
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine,
                                                          queue: nil) { [weak self] _ in
            Log.warn("tts: audio output changed; ending the current clip")
            self?.finishCurrent()
        }
    }

    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }

    func start() throws {
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
    }

    func play(_ samples: [Float], rate: Double, token: SpeechCancelToken, onStart: @escaping @Sendable () -> Void) async {
        guard !samples.isEmpty, !token.isCancelled else { return }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: Self.format, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        let seconds = Double(samples.count) / SpeechAudio.sampleRate / max(rate, 0.1)

        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            let mySession = lock.withLock { () -> Int in
                session += 1
                waiter = cont
                return session
            }
            token.onCancel { [weak self] in self?.stop() }
            let started: Bool = playLock.withLock {
                if token.isCancelled { return false }
                do {
                    player.stop()
                    try configure(rate: rate)
                    try start()
                } catch {
                    Log.error("tts: audio output failed to start: \(error)")
                    return false
                }
                player.scheduleBuffer(buffer, at: nil, options: [], completionCallbackType: .dataPlayedBack) { [weak self] _ in
                    self?.finish(session: mySession)
                }
                player.play()
                return true
            }
            guard started else {
                finish(session: mySession)
                return
            }
            onStart()
            // Safety net: a completion callback that never comes must not leave the brain waiting for tts.finished.
            DispatchQueue.global().asyncAfter(deadline: .now() + seconds + 2) { [weak self] in
                self?.finish(session: mySession)
            }
        }
    }

    func stop() {
        playLock.withLock { player.stop() }
        finishCurrent()
    }

    /// Wires the time-pitch unit in only for rates other than 1. Call with the player stopped.
    private func configure(rate: Double) throws {
        let wantStretch = abs(rate - 1) >= 0.01
        if wantStretch { timePitch.rate = Float(rate) }
        guard wantStretch != stretching else { return }
        engine.disconnectNodeOutput(player)
        engine.disconnectNodeOutput(timePitch)
        if wantStretch {
            engine.connect(player, to: timePitch, format: Self.format)
            engine.connect(timePitch, to: engine.mainMixerNode, format: Self.format)
        } else {
            engine.connect(player, to: engine.mainMixerNode, format: Self.format)
        }
        stretching = wantStretch
    }

    private func finishCurrent() {
        let current = lock.withLock { session }
        finish(session: current)
    }

    private func finish(session finished: Int) {
        let w = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            guard finished == session else { return nil }
            let w = waiter
            waiter = nil
            return w
        }
        w?.resume()
    }
}
