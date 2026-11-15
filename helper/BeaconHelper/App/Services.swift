// Owner: Mac owner. Service protocols shared by every Helper module, plus the container that wires them.
// Each module (Accessibility, Input, Speech, HUD) provides a concrete type; Server/Routes.swift only talks to these protocols.
import AppKit
import Foundation

typealias JSONObject = [String: Any]

/// Contract error codes (spec section 4.5).
enum ErrorCode: String {
    case staleSnapshot = "stale_snapshot"
    case elementNotFound = "element_not_found"
    case actionUnsupported = "action_unsupported"
    case actionFailed = "action_failed"
    case permissionDenied = "permission_denied"
    case timeout
    case appNotFound = "app_not_found"
    case badRequest = "bad_request"
}

/// Throw this from any service to produce `{"ok": false, "error": {...}}` with HTTP 200.
struct HelperError: Error, CustomStringConvertible {
    let code: ErrorCode
    let message: String
    var extra: JSONObject = [:]
    init(_ code: ErrorCode, _ message: String, extra: JSONObject = [:]) {
        self.code = code
        self.message = message
        self.extra = extra
    }
    var description: String { "\(code.rawValue): \(message)" }
}

// MARK: - Accessibility (H2, H3)

protocol AccessibilityProviding: AnyObject, Sendable {
    /// Builds a snapshot of the frontmost app. Returns the snapshot object of spec section 5.1 *without* `ok`.
    /// `scope` is `window`, `focused` or `menu`.
    func snapshot(scope: String, maxElements: Int) async throws -> JSONObject
    /// Searches the full unfiltered tree; each match has the same fields as a snapshot element.
    func find(query: String, limit: Int) async throws -> [JSONObject]
    /// Full untruncated text of an element and its descendants.
    func read(snapshotID: String, elementID: Int) async throws -> String
    /// Performs `press`, `focus`, `set_value`, `show_menu`, `increment`, `decrement`. Returns `ax` or `click`.
    func act(snapshotID: String, elementID: Int, action: String, value: Any?) async throws -> String
    /// Screen-space frame (top-left origin, points) of a snapshot element, for the highlight ring and scrolling.
    func frame(snapshotID: String, elementID: Int) async throws -> CGRect
}

// MARK: - Input and apps (H3)

protocol InputProviding: AnyObject, Sendable {
    /// Types unicode text into the focused element. Returns characters typed.
    func type(text: String) async throws -> Int
    /// Sends a key chord such as `cmd+return`, `escape`, `tab`.
    func key(_ keys: String) async throws
    /// Scrolls by `amount` ("page" or a pixel count). `at` is a screen point to scroll over, if given.
    func scroll(direction: String, amount: String, at point: CGPoint?) async throws
    /// Launches or focuses an app by (fuzzy) name. Returns the resolved name and pid.
    func openApp(name: String) async throws -> (app: String, pid: Int32)
    func listApps() async -> (running: [String], installed: [String])
}

// MARK: - Speech (H4, H5)

protocol SpeechInputProviding: AnyObject, Sendable {
    /// Human-readable STT model name for /health.
    var modelName: String { get }
    /// Loads and prewarms the Whisper model from the local folder. Never downloads.
    func prewarm() async
    /// Opens the microphone and emits `listen.started` (and `stt.partial` events).
    func startListening()
    /// Closes the microphone, transcribes, and emits `stt.final` or `listen.cancelled`.
    func stopListening()
    /// Discards the current recording and emits `listen.cancelled` with the reason.
    func cancelListening(reason: String)
    var isListening: Bool { get }
}

protocol SpeechOutputProviding: AnyObject, Sendable {
    /// `ttskit` or `system`, for /health.
    var engineName: String { get }
    func prewarm() async
    /// Queues speech; `interrupt` flushes the queue first. Returns the speech id. Emits `tts.started`/`tts.finished`.
    func speak(text: String, interrupt: Bool, rate: Double?) -> String
    /// Silences audio within 100 ms and flushes the queue.
    func stop()
    var isSpeaking: Bool { get }
}

// MARK: - HUD (H6)

enum HUDState: String, CaseIterable {
    case idle, listening, thinking, acting, speaking, confirm, asking, error
}

enum SoundCue: String, CaseIterable {
    case listenStart = "listen_start"   // short rising tone
    case listenStop = "listen_stop"     // short falling tone
    case tick                           // thinking tick
    case action                         // single click
    case error                          // low double tone
}

@MainActor
protocol HUDProviding: AnyObject {
    func show()
    /// Updates any field that is non-nil. `caption` replaces the transcript line when set.
    func update(state: HUDState?, transcript: String?, caption: String?, latencyMS: Int?)
    /// Draws the high-contrast ring around a screen rect (top-left origin, points) for 1.5 s.
    func highlight(_ rect: CGRect)
    func setOffline(_ offline: Bool)
    func play(_ cue: SoundCue)
    /// Window numbers of the Helper's own panels, so snapshots can ignore them.
    var windowNumbers: [Int] { get }
}

// MARK: - Container

/// Global wiring. Set once in AppDelegate before the server starts.
final class Services: @unchecked Sendable {
    nonisolated(unsafe) static var shared: Services!

    let accessibility: AccessibilityProviding
    let input: InputProviding
    let speechIn: SpeechInputProviding
    let speechOut: SpeechOutputProviding
    let events: EventHub
    let hud: HUDProviding

    @MainActor
    init(accessibility: AccessibilityProviding, input: InputProviding,
         speechIn: SpeechInputProviding, speechOut: SpeechOutputProviding,
         events: EventHub, hud: HUDProviding) {
        self.accessibility = accessibility
        self.input = input
        self.speechIn = speechIn
        self.speechOut = speechOut
        self.events = events
        self.hud = hud
    }
}
