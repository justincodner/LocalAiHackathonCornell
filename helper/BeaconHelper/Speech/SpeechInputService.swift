// Owner: Mac owner. STUB - replaced by the H4 speech-in workstream.
import Foundation

final class SpeechInputService: SpeechInputProviding, @unchecked Sendable {
    let events: EventHub
    init(events: EventHub) { self.events = events }
    var modelName: String { Config.sttModel }
    var isListening = false
    func prewarm() async {}
    func startListening() { isListening = true; events.emit("listen.started") }
    func stopListening() { isListening = false; events.emit("listen.cancelled", ["reason": "stt not built yet"]) }
    func cancelListening(reason: String) { isListening = false; events.emit("listen.cancelled", ["reason": reason]) }
}
