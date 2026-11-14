// Owner: Mac owner. STUB - replaced by the H5 speech-out workstream.
import Foundation

final class SpeechOutputService: SpeechOutputProviding, @unchecked Sendable {
    let events: EventHub
    init(events: EventHub) { self.events = events }
    var engineName: String { Config.tts }
    var isSpeaking: Bool { false }
    func prewarm() async {}
    func speak(text: String, interrupt: Bool, rate: Double?) -> String { "sp_0000" }
    func stop() {}
}
