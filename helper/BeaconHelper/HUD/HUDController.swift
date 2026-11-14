// Owner: Mac owner. STUB - replaced by the H6 HUD workstream.
import AppKit

@MainActor
final class HUDController: HUDProviding {
    func show() {}
    func update(state: HUDState?, transcript: String?, caption: String?, latencyMS: Int?) {}
    func highlight(_ rect: CGRect) {}
    func setOffline(_ offline: Bool) {}
    func play(_ cue: SoundCue) {}
    var windowNumbers: [Int] { [] }
}
