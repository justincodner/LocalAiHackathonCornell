// Owner: Mac owner. macOS Color Filters (System Settings > Accessibility > Display > Color Filters) for the
// Brain's color-filter commands (brain/beacon_brain/color_filter.py).
//
// There is no public API for this setting. System Settings itself calls four functions exported by
// /System/Library/Frameworks/MediaAccessibility.framework (MADisplayFilterPref{Get,Set}{CategoryEnabled,Type}),
// which apply the filter system-wide immediately and save it to the `com.apple.mediaaccessibility` preferences.
// The Helper resolves them at run time with dlsym, so a macOS release that renames them makes the feature report
// `action_unsupported` instead of crashing. Every change is verified by re-reading the saved preference values
// (`__Color__-MADisplayFilterCategoryEnabled`, `__Color__-MADisplayFilterType`) through CFPreferences, which is
// independent of the setter; a change that does not read back is reported as `action_failed`, never as success.
//
// Only the color-filter "enabled" flag and filter type are touched. Intensity, tint color and every other
// accessibility setting are left alone.
import Darwin
import Foundation

/// The filters in the System Settings "Filter type" menu, with the MADisplayFilterType value each one is saved as.
enum ColorFilterKind: String, CaseIterable, Sendable {
    case grayscale       // "Grayscale"
    case protanopia      // "Red/Green filter (Protanopia)"
    case deuteranopia    // "Green/Red filter (Deuteranopia)"
    case tritanopia      // "Blue/Yellow filter (Tritanopia)"
    case tint            // "Color Tint"

    var typeValue: Int {
        switch self {
        case .grayscale: return 1
        case .protanopia: return 2
        case .deuteranopia: return 4
        case .tritanopia: return 8
        case .tint: return 16
        }
    }

    init?(typeValue: Int) {
        guard let kind = Self.allCases.first(where: { $0.typeValue == typeValue }) else { return nil }
        self = kind
    }
}

struct ColorFilterState: Equatable, Sendable {
    var enabled: Bool
    var typeValue: Int

    var filter: ColorFilterKind? { ColorFilterKind(typeValue: typeValue) }

    var json: JSONObject {
        ["enabled": enabled, "filter": filter?.rawValue ?? "unknown", "type_value": typeValue]
    }
}

/// The operating-system side, behind a protocol so unit tests never change the real display.
protocol ColorFilterBackend: Sendable {
    /// False when the private MediaAccessibility functions could not be found on this macOS version.
    var isAvailable: Bool { get }
    /// The saved state, re-read from the preferences store (not from the setter's own cache).
    func read() -> ColorFilterState
    /// Selects `typeValue` (when given) and then turns the filter on or off.
    func write(enabled: Bool, typeValue: Int?)
}

protocol ColorFilterProviding: AnyObject, Sendable {
    /// `{"enabled", "filter", "type_value", "available"}`.
    func state() async throws -> JSONObject
    /// Applies the change, then waits for it to read back. Returns the verified state plus `previous`.
    func set(enabled: Bool, filter: String?) async throws -> JSONObject
}

final class ColorFilterService: ColorFilterProviding, @unchecked Sendable {
    private let backend: ColorFilterBackend
    private let attempts: Int
    private let pollInterval: TimeInterval
    private let lock = NSLock()  // one change at a time

    init(backend: ColorFilterBackend = MediaAccessibilityColorFilters(), attempts: Int = 10,
         pollInterval: TimeInterval = 0.05) {
        self.backend = backend
        self.attempts = max(attempts, 1)
        self.pollInterval = pollInterval
    }

    func state() async throws -> JSONObject {
        try requireAvailable()
        var result = backend.read().json
        result["available"] = true
        return result
    }

    func set(enabled: Bool, filter: String?) async throws -> JSONObject {
        try requireAvailable()
        var kind: ColorFilterKind?
        if let filter {
            guard let parsed = ColorFilterKind(rawValue: filter) else {
                let names = ColorFilterKind.allCases.map(\.rawValue).joined(separator: ", ")
                throw HelperError(.badRequest, "filter must be one of \(names)")
            }
            kind = parsed
        } else if enabled {
            throw HelperError(.badRequest, "turning a color filter on needs 'filter'")
        }
        return try lock.withLock {
            let previous = backend.read()
            backend.write(enabled: enabled, typeValue: kind?.typeValue)
            var now = backend.read()
            for _ in 1..<attempts where !matches(now, enabled: enabled, kind: kind) {
                Thread.sleep(forTimeInterval: pollInterval)
                now = backend.read()
            }
            guard matches(now, enabled: enabled, kind: kind) else {
                Log.warn("color filter change did not read back: wanted enabled=\(enabled) filter=\(kind?.rawValue ?? "-"), "
                         + "read enabled=\(now.enabled) type=\(now.typeValue)")
                throw HelperError(.actionFailed, "The color filter change could not be verified",
                                  extra: ["state": now.json])
            }
            Log.info("color filter enabled=\(now.enabled) type=\(now.typeValue) (was \(previous.enabled)/\(previous.typeValue))")
            var result = now.json
            result["previous"] = previous.json
            result["verified"] = true
            return result
        }
    }

    private func matches(_ state: ColorFilterState, enabled: Bool, kind: ColorFilterKind?) -> Bool {
        state.enabled == enabled && (kind == nil || state.typeValue == kind!.typeValue)
    }

    private func requireAvailable() throws {
        guard backend.isAvailable else {
            throw HelperError(.actionUnsupported,
                              "Color filters can't be changed by Beacon on this version of macOS. "
                              + "Use System Settings > Accessibility > Display > Color Filters.")
        }
    }
}

/// The real backend: MediaAccessibility's display-filter preference functions, as System Settings uses them.
final class MediaAccessibilityColorFilters: ColorFilterBackend, @unchecked Sendable {
    /// The "Color" display-filter category (`__Color__-...` preference keys).
    static let colorCategory: Int32 = 1
    static let domain = "com.apple.mediaaccessibility" as CFString
    static let enabledKey = "__Color__-MADisplayFilterCategoryEnabled" as CFString
    static let typeKey = "__Color__-MADisplayFilterType" as CFString

    private typealias SetEnabled = @convention(c) (Int32, Bool) -> Void
    private typealias SetType = @convention(c) (Int32, Int32) -> Void

    private let setEnabled: SetEnabled?
    private let setType: SetType?

    init(path: String = "/System/Library/Frameworks/MediaAccessibility.framework/MediaAccessibility") {
        let handle = dlopen(path, RTLD_LAZY)
        func symbol<T>(_ name: String, as _: T.Type) -> T? {
            guard let handle, let pointer = dlsym(handle, name) else { return nil }
            return unsafeBitCast(pointer, to: T.self)
        }
        setEnabled = symbol("MADisplayFilterPrefSetCategoryEnabled", as: SetEnabled.self)
        setType = symbol("MADisplayFilterPrefSetType", as: SetType.self)
        if setEnabled == nil || setType == nil {
            Log.warn("MediaAccessibility display-filter functions not found; color filters are unavailable")
        }
    }

    var isAvailable: Bool { setEnabled != nil && setType != nil }

    func read() -> ColorFilterState {
        CFPreferencesAppSynchronize(Self.domain)
        let enabled = CFPreferencesCopyAppValue(Self.enabledKey, Self.domain) as? Bool ?? false
        let type = (CFPreferencesCopyAppValue(Self.typeKey, Self.domain) as? NSNumber)?.intValue ?? 0
        return ColorFilterState(enabled: enabled, typeValue: type)
    }

    func write(enabled: Bool, typeValue: Int?) {
        if let typeValue { setType?(Self.colorCategory, Int32(typeValue)) }
        setEnabled?(Self.colorCategory, enabled)
    }
}
