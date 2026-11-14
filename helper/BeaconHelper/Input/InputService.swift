// Owner: Mac owner. STUB - replaced by the H3 input workstream.
import AppKit
import Foundation

final class InputService: InputProviding, @unchecked Sendable {
    func type(text: String) async throws -> Int { throw HelperError(.actionUnsupported, "not built yet") }
    func key(_ keys: String) async throws { throw HelperError(.actionUnsupported, "not built yet") }
    func scroll(direction: String, amount: String, at point: CGPoint?) async throws { throw HelperError(.actionUnsupported, "not built yet") }
    func openApp(name: String) async throws -> (app: String, pid: Int32) { throw HelperError(.actionUnsupported, "not built yet") }
    func listApps() async -> (running: [String], installed: [String]) { ([], []) }
}
