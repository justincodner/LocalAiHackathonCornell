// Owner: Mac owner. JSON helpers and request-argument parsing shared by all routes.
import Foundation

enum JSON {
    /// Serialises a JSON object. NSNull/optional nils become `null`; CGFloat/Double are kept as numbers.
    static func data(_ object: Any) -> Data {
        let clean = sanitize(object)
        return (try? JSONSerialization.data(withJSONObject: clean, options: [.sortedKeys, .withoutEscapingSlashes]))
            ?? Data(#"{"ok":false,"error":{"code":"action_failed","message":"unserialisable response"}}"#.utf8)
    }

    static func string(_ object: Any) -> String? {
        String(data: data(object), encoding: .utf8)
    }

    static func parse(_ data: Data) throws -> JSONObject {
        if data.isEmpty { return [:] }
        guard let obj = try? JSONSerialization.jsonObject(with: data), let dict = obj as? JSONObject else {
            throw HelperError(.badRequest, "body must be a JSON object")
        }
        return dict
    }

    /// Replaces Swift optionals with NSNull and converts CGRect/CGPoint-free values into JSON-safe types.
    static func sanitize(_ value: Any) -> Any {
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .optional {
            guard let child = mirror.children.first else { return NSNull() }
            return sanitize(child.value)
        }
        switch value {
        case let d as [String: Any]: return d.mapValues { sanitize($0) }
        case let a as [Any]: return a.map { sanitize($0) }
        case let f as CGFloat: return Double(f)
        case let f as Float: return Double(f)
        default: return value
        }
    }
}

/// Typed accessors for a parsed request body. Missing or wrong-typed required fields throw `bad_request`.
struct Args {
    let raw: JSONObject

    func string(_ key: String) throws -> String {
        guard let v = raw[key] as? String else { throw HelperError(.badRequest, "missing string field '\(key)'") }
        return v
    }

    func optString(_ key: String) -> String? { raw[key] as? String }

    func int(_ key: String) throws -> Int {
        guard let v = optInt(key) else { throw HelperError(.badRequest, "missing integer field '\(key)'") }
        return v
    }

    func optInt(_ key: String) -> Int? {
        if let n = raw[key] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() { return n.intValue }
        if let s = raw[key] as? String { return Int(s) }
        return nil
    }

    func optDouble(_ key: String) -> Double? {
        if let n = raw[key] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() { return n.doubleValue }
        return nil
    }

    func optObject(_ key: String) -> JSONObject? { raw[key] as? JSONObject }
}
