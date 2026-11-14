// Owner: Mac owner. Plays the bundled HUD sound cues (spec 9.2) with preloaded AVAudioPlayers.
import AVFoundation
import Foundation

@MainActor
final class SoundPlayer {
    static let bundleName = "BeaconHelper_BeaconHelper.bundle"
    private var players: [SoundCue: AVAudioPlayer] = [:]

    init() {
        guard let dir = Self.soundsDirectory() else {
            Log.warn("HUD sounds folder not found; sound cues are disabled")
            return
        }
        for cue in SoundCue.allCases {
            let url = dir.appendingPathComponent(cue.rawValue + ".wav")
            do {
                let p = try AVAudioPlayer(contentsOf: url)
                p.prepareToPlay()
                players[cue] = p
            } catch {
                Log.warn("HUD sound \(cue.rawValue) failed to load from \(url.path): \(error)")
            }
        }
        Log.info("HUD sounds loaded: \(players.count)/\(SoundCue.allCases.count) from \(dir.path)")
    }

    func play(_ cue: SoundCue) {
        guard let p = players[cue] else {
            Log.warn("HUD sound \(cue.rawValue) unavailable")
            return
        }
        p.currentTime = 0
        p.play()
    }

    /// Finds the `Sounds` folder without `Bundle.module`, whose generated accessor crashes when the binary is
    /// repackaged into a .app. Checks the SwiftPM resource bundle in Resources, next to the executable, and in
    /// the bundle root, then a plain `Sounds` folder in Resources.
    static func soundsDirectory() -> URL? {
        let fm = FileManager.default
        var bundles: [URL] = []
        if let r = Bundle.main.resourceURL { bundles.append(r.appendingPathComponent(bundleName)) }
        if let e = Bundle.main.executableURL { bundles.append(e.deletingLastPathComponent().appendingPathComponent(bundleName)) }
        bundles.append(Bundle.main.bundleURL.appendingPathComponent(bundleName))

        var candidates: [URL] = []
        for b in bundles {
            candidates.append(b.appendingPathComponent("Sounds"))
            candidates.append(b.appendingPathComponent("Contents/Resources/Sounds"))
        }
        if let r = Bundle.main.resourceURL { candidates.append(r.appendingPathComponent("Sounds")) }

        for c in candidates {
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: c.path, isDirectory: &isDir), isDir.boolValue { return c }
        }
        return nil
    }
}
