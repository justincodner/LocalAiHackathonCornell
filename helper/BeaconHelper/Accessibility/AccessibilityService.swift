// Owner: Mac owner. STUB - replaced by the H2/H3 accessibility workstream.
import AppKit
import Foundation

final class AccessibilityService: AccessibilityProviding, @unchecked Sendable {
    func snapshot(scope: String, maxElements: Int) async throws -> JSONObject { throw HelperError(.actionUnsupported, "not built yet") }
    func find(query: String, limit: Int) async throws -> [JSONObject] { throw HelperError(.actionUnsupported, "not built yet") }
    func read(snapshotID: String, elementID: Int) async throws -> String { throw HelperError(.actionUnsupported, "not built yet") }
    func act(snapshotID: String, elementID: Int, action: String, value: Any?) async throws -> String { throw HelperError(.actionUnsupported, "not built yet") }
    func frame(snapshotID: String, elementID: Int) async throws -> CGRect { throw HelperError(.actionUnsupported, "not built yet") }
}
