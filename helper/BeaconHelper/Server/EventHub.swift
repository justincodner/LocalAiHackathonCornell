// Owner: Mac owner. Broadcasts Helper events to every connected /events WebSocket client (spec 4.6).
import FlyingFox
import Foundation

final class EventHub: @unchecked Sendable {
    private let lock = NSLock()
    private var clients: [UUID: AsyncStream<WSMessage>.Continuation] = [:]
    /// Last few events, kept for tests and the menu-bar debug view.
    private(set) var recent: [JSONObject] = []

    private var observers: [@Sendable (JSONObject) -> Void] = []
    private let moveHUD: (@Sendable (HUDPlacement) -> Void)?
    /// Brings back a minimized HUD. The flag is true when the utterance was only the wake phrase.
    private let restoreHUD: (@Sendable (_ wakeOnly: Bool) -> Void)?
    private let isHUDMinimized: (@Sendable () -> Bool)?

    init(moveHUD: (@Sendable (HUDPlacement) -> Void)? = nil,
         restoreHUD: (@Sendable (_ wakeOnly: Bool) -> Void)? = nil,
         isHUDMinimized: (@Sendable () -> Bool)? = nil) {
        self.moveHUD = moveHUD
        self.restoreHUD = restoreHUD
        self.isHUDMinimized = isHUDMinimized
    }

    var clientCount: Int { lock.withLock { clients.count } }

    // MARK: Selection focus

    /// Text from a screen region the user selected (Control+Option+S). The next `stt.final`, spoken or typed,
    /// carries it as `focus` and consumes it.
    private var pendingFocus: (id: Int, focus: JSONObject)?
    private var focusCounter = 0

    /// Stores focus for the next utterance, replacing any earlier one. Returns an id for `clearPendingFocus`.
    @discardableResult
    func setPendingFocus(_ focus: JSONObject) -> Int {
        lock.withLock {
            focusCounter += 1
            pendingFocus = (focusCounter, focus)
            return focusCounter
        }
    }

    /// Drops the pending focus (only if it is still the one with this id, when given). True if one was dropped.
    @discardableResult
    func clearPendingFocus(id: Int? = nil) -> Bool {
        lock.withLock {
            guard let current = pendingFocus, id == nil || current.id == id else { return false }
            pendingFocus = nil
            return true
        }
    }

    var hasPendingFocus: Bool { lock.withLock { pendingFocus != nil } }

    /// In-process listeners (the app coordinator uses this to drive the HUD and sounds).
    func observe(_ observer: @escaping @Sendable (JSONObject) -> Void) {
        lock.withLock { observers.append(observer) }
    }

    /// Emits `{"type": type, "ts": <unix ms>, ...fields}` to every client.
    func emit(_ type: String, _ fields: JSONObject = [:], localFields: JSONObject = [:]) {
        var fields = fields
        if type == "stt.final", let text = fields["text"] as? String {
            // While the HUD is minimized, "Hey Beacon" brings it back and is not a task for the Brain.
            if let restoreHUD, isHUDMinimized?() == true, let wake = WakePhrase.match(text) {
                switch wake {
                case .only:
                    restoreHUD(true)
                    Log.info("wake phrase while minimized; HUD restored, utterance not forwarded")
                    return
                case let .followedBy(rest):
                    restoreHUD(false)
                    fields["text"] = rest
                }
            }
            if let moveHUD, let text = fields["text"] as? String, let placement = HUDPlacement(utterance: text) {
                moveHUD(placement)
                return
            }
        }
        if type == "stt.final", fields["focus"] == nil,
           let focus = lock.withLock({ () -> JSONObject? in
               defer { pendingFocus = nil }
               return pendingFocus?.focus
           }) {
            fields["focus"] = focus
        }
        var event = fields
        event["type"] = type
        event["ts"] = Int(Date().timeIntervalSince1970 * 1000)
        guard let text = JSON.string(event) else { return }
        let (targets, obs) = lock.withLock {
            recent.append(event)
            if recent.count > 50 { recent.removeFirst(recent.count - 50) }
            return (Array(clients.values), observers)
        }
        for c in targets { c.yield(.text(text)) }
        let localEvent = event.merging(localFields) { original, _ in original }
        for o in obs { o(localEvent) }
        Log.info("event \(text)")
    }

    fileprivate func add(_ id: UUID, _ c: AsyncStream<WSMessage>.Continuation) {
        lock.withLock { clients[id] = c }
    }

    fileprivate func remove(_ id: UUID) {
        _ = lock.withLock { clients.removeValue(forKey: id) }
    }
}

/// WebSocket handler for `GET /events`. The Brain only listens; anything it sends is ignored.
struct EventsSocketHandler: WSMessageHandler {
    let hub: EventHub

    func makeMessages(for client: AsyncStream<WSMessage>) async throws -> AsyncStream<WSMessage> {
        let id = UUID()
        let (stream, continuation) = AsyncStream<WSMessage>.makeStream()
        hub.add(id, continuation)
        Task {
            for await message in client {
                if case .close = message { break }
            }
            hub.remove(id)
            continuation.finish()
        }
        return stream
    }
}
