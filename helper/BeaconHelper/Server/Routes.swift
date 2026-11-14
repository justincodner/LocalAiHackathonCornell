// Owner: Mac owner. HTTP + WebSocket server on 127.0.0.1:8765. Implements contracts/openapi.yaml.
import AppKit
import FlyingFox
import FlyingSocks
import Foundation

final class HelperServer: @unchecked Sendable {
    let services: Services
    private var server: HTTPServer?
    private var utteranceCounter = 0
    private let counterLock = NSLock()

    init(services: Services) {
        self.services = services
    }

    /// Starts listening. Loopback only unless BEACON_DEV_LAN=1 (which then requires BEACON_TOKEN).
    func start() async throws {
        let address: any SocketAddress
        if Config.devLAN {
            guard !Config.token.isEmpty else {
                Log.error("BEACON_DEV_LAN=1 requires BEACON_TOKEN; refusing to listen on the LAN")
                throw HelperError(.permissionDenied, "BEACON_DEV_LAN=1 requires BEACON_TOKEN")
            }
            address = sockaddr_in.inet(port: Config.port)
            Log.warn("DEV LAN MODE: listening on all interfaces with token auth. Never use for the demo.")
        } else {
            address = try sockaddr_in.inet(ip4: "127.0.0.1", port: Config.port)
        }
        let server = HTTPServer(address: address, timeout: 60, logger: .disabled)
        self.server = server
        await register(on: server)
        Log.info("Helper listening on \(Config.devLAN ? "0.0.0.0" : "127.0.0.1"):\(Config.port)")
        try await server.run()
    }

    // MARK: - Routing

    private func register(on server: HTTPServer) async {
        let s = services
        await server.appendRoute("GET /events", to: .webSocket(EventsSocketHandler(hub: s.events)))

        await route(server, "GET /health") { _ in await self.health() }

        await route(server, "POST /ax/snapshot") { a in
            let scope = a.optString("scope") ?? "window"
            guard ["window", "focused", "menu"].contains(scope) else {
                throw HelperError(.badRequest, "scope must be window, focused or menu")
            }
            let max = min(max(a.optInt("max_elements") ?? 250, 1), 1000)
            return try await s.accessibility.snapshot(scope: scope, maxElements: max)
        }
        await route(server, "POST /ax/find") { a in
            let matches = try await s.accessibility.find(query: try a.string("query"), limit: a.optInt("limit") ?? 10)
            return ["matches": matches]
        }
        await route(server, "POST /ax/read") { a in
            ["text": try await s.accessibility.read(snapshotID: try a.string("snapshot_id"), elementID: try a.int("element_id"))]
        }
        await route(server, "POST /ax/act") { a in
            let action = try a.string("action")
            let allowed = ["press", "focus", "set_value", "show_menu", "increment", "decrement"]
            guard allowed.contains(action) else {
                throw HelperError(.badRequest, "action must be one of \(allowed.joined(separator: ", "))")
            }
            if action == "set_value", a.raw["value"] == nil {
                throw HelperError(.badRequest, "set_value needs 'value'")
            }
            let via = try await s.accessibility.act(snapshotID: try a.string("snapshot_id"),
                                                    elementID: try a.int("element_id"),
                                                    action: action, value: a.raw["value"])
            await MainActor.run { s.hud.play(.action) }
            return ["performed_via": via]
        }

        await route(server, "POST /input/type") { a in
            let text = try a.string("text")
            guard !text.isEmpty, text.count <= 2000 else { throw HelperError(.badRequest, "text must be 1 to 2000 characters") }
            return ["chars": try await s.input.type(text: text)]
        }
        await route(server, "POST /input/key") { a in
            try await s.input.key(try a.string("keys"))
            return [:]
        }
        await route(server, "POST /input/scroll") { a in
            let direction = try a.string("direction")
            guard ["up", "down", "left", "right"].contains(direction) else {
                throw HelperError(.badRequest, "direction must be up, down, left or right")
            }
            var amount = "page"
            if let n = a.optInt("amount") { amount = String(n) } else if let str = a.optString("amount") { amount = str }
            var point: CGPoint?
            if let sid = a.optString("snapshot_id"), let eid = a.optInt("element_id") {
                let f = try await s.accessibility.frame(snapshotID: sid, elementID: eid)
                point = CGPoint(x: f.midX, y: f.midY)
            }
            try await s.input.scroll(direction: direction, amount: amount, at: point)
            return [:]
        }

        await route(server, "POST /app/open") { a in
            let r = try await s.input.openApp(name: try a.string("name"))
            return ["app": r.app, "pid": Int(r.pid)]
        }
        await route(server, "GET /app/list") { _ in
            let r = await s.input.listApps()
            return ["running": r.running, "installed": r.installed]
        }

        await route(server, "POST /tts/speak") { a in
            let text = try a.string("text")
            let priority = a.optString("priority") ?? "normal"
            guard ["normal", "interrupt"].contains(priority) else { throw HelperError(.badRequest, "priority must be normal or interrupt") }
            var rate = a.optDouble("rate")
            if let r = rate { rate = min(max(r, 0.5), 1.5) }
            let id = s.speechOut.speak(text: text, interrupt: priority == "interrupt", rate: rate)
            return ["speech_id": id]
        }
        await route(server, "POST /tts/stop") { _ in
            s.speechOut.stop()
            return [:]
        }

        await route(server, "POST /stt/listen") { a in
            switch try a.string("mode") {
            case "start":
                if s.speechOut.isSpeaking { s.speechOut.stop() }
                s.speechIn.startListening()
                await MainActor.run { s.hud.update(state: .listening, transcript: "", caption: nil, latencyMS: nil) }
            case "stop": s.speechIn.stopListening()
            default: throw HelperError(.badRequest, "mode must be start or stop")
            }
            return [:]
        }

        await route(server, "POST /hud") { a in
            let stateRaw = try a.string("state")
            guard let state = HUDState(rawValue: stateRaw) else { throw HelperError(.badRequest, "unknown HUD state '\(stateRaw)'") }
            var rect: CGRect?
            if let h = a.optObject("highlight"), let sid = h["snapshot_id"] as? String,
               let eid = (h["element_id"] as? NSNumber)?.intValue {
                rect = try await s.accessibility.frame(snapshotID: sid, elementID: eid)
            }
            let transcript = a.optString("transcript"), caption = a.optString("caption"), latency = a.optInt("latency_ms")
            let ring = rect
            await MainActor.run {
                s.hud.update(state: state, transcript: transcript, caption: caption, latencyMS: latency)
                if let ring { s.hud.highlight(ring) }
                if state == .error { s.hud.play(.error) }
            }
            return [:]
        }

        await route(server, "POST /screen/capture") { a in
            var rect: CGRect?
            if let sid = a.optString("snapshot_id"), let eid = a.optInt("element_id") {
                rect = try await s.accessibility.frame(snapshotID: sid, elementID: eid)
            }
            return ["png_base64": try await ScreenCapture.pngBase64(rect: rect)]
        }

        await route(server, "POST /debug/utterance") { a in
            let text = try a.string("text")
            s.events.emit("stt.final", ["utterance_id": self.nextUtteranceID(), "text": text, "audio_ms": 0, "stt_ms": 0])
            await MainActor.run { s.hud.update(state: .thinking, transcript: text, caption: nil, latencyMS: nil) }
            return [:]
        }
    }

    func nextUtteranceID() -> String {
        counterLock.withLock {
            utteranceCounter += 1
            return String(format: "u_%04d", utteranceCounter)
        }
    }

    /// Registers a JSON route. The handler returns the success fields; `ok: true` is added. Thrown HelperErrors become
    /// `{"ok": false, "error": {...}}` with HTTP 200, per the contract.
    private func route(_ server: HTTPServer, _ path: HTTPRoute,
                       _ body: @escaping @Sendable (Args) async throws -> JSONObject) async {
        await server.appendRoute(path) { request in await self.respond(request, body) }
    }

    private func respond(_ request: HTTPRequest, _ body: @Sendable (Args) async throws -> JSONObject) async -> HTTPResponse {
        let started = Date()
        var result: JSONObject
        do {
            if Config.devLAN, request.headers[HTTPHeader("X-Beacon-Token")] != Config.token {
                throw HelperError(.permissionDenied, "missing or wrong X-Beacon-Token")
            }
            let args = Args(raw: try JSON.parse(try await request.bodyData))
            result = try await body(args)
            result["ok"] = true
        } catch let e as HelperError {
            var err: JSONObject = ["code": e.code.rawValue, "message": e.message]
            for (k, v) in e.extra { err[k] = v }
            result = ["ok": false, "error": err]
        } catch {
            result = ["ok": false, "error": ["code": ErrorCode.actionFailed.rawValue, "message": "\(error)"]]
        }
        let ms = Int(Date().timeIntervalSince(started) * 1000)
        Log.info("\(request.method.rawValue) \(request.path) ok=\(result["ok"] as? Bool ?? false) \(ms)ms")
        return HTTPResponse(statusCode: .ok, headers: [.contentType: "application/json"], body: JSON.data(result))
    }

    private func health() async -> JSONObject {
        let perms = Permissions.status()
        return [
            "version": Config.version,
            "contract_version": Config.contractVersion,
            "permissions": ["accessibility": perms.accessibility, "microphone": perms.microphone,
                            "screen_recording": perms.screenRecording],
            "stt_model": services.speechIn.modelName,
            "tts_engine": services.speechOut.engineName,
            "online": Connectivity.shared.isOnline,
            "ptt_key": Config.pttKey,
            "dev_lan": Config.devLAN,
        ]
    }
}
