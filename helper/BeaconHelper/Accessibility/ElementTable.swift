// Owner: Mac owner (H2). snapshot_id -> [element_id -> element] for the last 3 snapshots (spec 4.4).
import Foundation

final class ElementTable<Element>: @unchecked Sendable {
    let capacity: Int
    private let lock = NSLock()
    private var counter = 0
    private var order: [String] = []
    private var entries: [String: [Int: Element]] = [:]

    init(capacity: Int = 3) {
        self.capacity = capacity
    }

    /// Reserves the next id (`s_0001`, `s_0002`, ...).
    func nextID() -> String {
        lock.withLock {
            counter += 1
            return Self.format(counter)
        }
    }

    static func format(_ n: Int) -> String { String(format: "s_%04d", n) }

    /// Stores the elements of a snapshot, evicting the oldest beyond `capacity`.
    func register(_ id: String, _ elements: [Int: Element]) {
        lock.withLock {
            entries[id] = elements
            order.removeAll { $0 == id }
            order.append(id)
            while order.count > capacity {
                entries[order.removeFirst()] = nil
            }
        }
    }

    func resolve(snapshotID: String, elementID: Int) throws -> Element {
        try lock.withLock {
            guard let table = entries[snapshotID] else {
                throw HelperError(.staleSnapshot, "\(snapshotID) expired; take a new snapshot")
            }
            guard let el = table[elementID] else {
                throw HelperError(.elementNotFound, "element \(elementID) is not in \(snapshotID)")
            }
            return el
        }
    }

    var liveIDs: [String] { lock.withLock { order } }
}
