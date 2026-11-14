// Owner: Mac owner. Broadcasts Helper events to every connected /events WebSocket client (spec 4.6).
import FlyingFox
import Foundation

final class EventHub: @unchecked Sendable {
    private let lock = NSLock()
    private var clients: [UUID: AsyncStream<WSMessage>.Continuation] = [:]
    /// Last few events, kept for tests and the menu-bar debug view.
    private(set) var recent: [JSONObject] = []

    private var observers: [@Sendable (JSONObject) -> Void] = []

    var clientCount: Int { lock.withLock { clients.count } }

    /// In-process listeners (the app coordinator uses this to drive the HUD and sounds).
    func observe(_ observer: @escaping @Sendable (JSONObject) -> Void) {
        lock.withLock { observers.append(observer) }
    }

    /// Emits `{"type": type, "ts": <unix ms>, ...fields}` to every client.
    func emit(_ type: String, _ fields: JSONObject = [:]) {
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
        for o in obs { o(event) }
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
