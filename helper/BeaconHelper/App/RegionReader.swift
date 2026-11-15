// Owner: Mac owner. "Ask about part of the screen": turns a dragged rectangle into text the text-only models
// can use. The region is captured on-device, read with Vision OCR, and combined with the labels and values of
// accessibility elements inside it. Nothing leaves the Mac.
import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit
@preconcurrency import Vision

/// Coordinate helpers for the selection rectangle. Pure, so they are unit tested.
enum RegionGeometry {
    struct Display: Equatable {
        let id: CGDirectDisplayID
        /// Global frame, top-left origin of the primary display, in points (CGDisplayBounds).
        let frame: CGRect
    }

    /// Rectangle spanned by two drag points, in either order.
    static func rect(from a: CGPoint, to b: CGPoint) -> CGRect {
        CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
    }

    /// AppKit global rect (bottom-left origin) to a top-left-origin global rect. The flip is its own inverse.
    static func topLeftRect(fromAppKit r: CGRect, primaryScreenHeight h: CGFloat) -> CGRect {
        CGRect(x: r.origin.x, y: h - r.origin.y - r.height, width: r.width, height: r.height)
    }

    /// Too small to be a deliberate selection (a click without a drag).
    static func isTooSmall(_ r: CGRect) -> Bool { r.width < 8 || r.height < 8 }

    /// The display that shows most of `rect`, and the rect clipped to it: globally and in that display's own
    /// points (top-left origin), which is what ScreenCaptureKit's `sourceRect` expects. Nil if off every display.
    static func captureTarget(for rect: CGRect, displays: [Display]) -> (display: Display, global: CGRect, local: CGRect)? {
        let scored = displays.map { d -> (Display, CGRect) in (d, rect.intersection(d.frame)) }
            .filter { !$0.1.isNull && $0.1.width > 0 && $0.1.height > 0 }
        guard let (display, clipped) = scored.max(by: { $0.1.width * $0.1.height < $1.1.width * $1.1.height }) else {
            return nil
        }
        let local = clipped.offsetBy(dx: -display.frame.minX, dy: -display.frame.minY)
        return (display, clipped, local)
    }

    /// Active displays with their global frames.
    static func activeDisplays() -> [Display] {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0 else { return [] }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &ids, &count) == .success else { return [] }
        return ids.prefix(Int(count)).map { Display(id: $0, frame: CGDisplayBounds($0)) }
    }
}

/// Builds the `focus` object attached to the next `stt.final`. Pure, so it is unit tested.
enum SelectionFocus {
    /// The Brain bounds it again; this keeps events small.
    static let maxChars = 4000

    /// One OCR line: text plus its Vision bounding box (normalised, bottom-left origin).
    struct OCRLine: Equatable {
        let text: String
        let box: CGRect
    }

    /// Lines in reading order: top to bottom, then left to right for lines at the same height.
    static func readingOrder(_ lines: [OCRLine]) -> String {
        let sorted = lines.sorted { a, b in
            let tolerance = min(a.box.height, b.box.height) / 2
            if abs(a.box.midY - b.box.midY) > tolerance { return a.box.midY > b.box.midY }
            return a.box.minX < b.box.minX
        }
        var out: [String] = []
        var previous: OCRLine?
        for line in sorted {
            let text = line.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            if let p = previous, abs(p.box.midY - line.box.midY) <= min(p.box.height, line.box.height) / 2,
               !out.isEmpty {
                out[out.count - 1] += "  " + text      // same row: keep table cells together
            } else {
                out.append(text)
            }
            previous = line
        }
        return out.joined(separator: "\n")
    }

    /// Labels and values of snapshot elements whose frame lies mostly inside `region` (both top-left global).
    static func elementLines(_ elements: [JSONObject], region: CGRect) -> [String] {
        var seen = Set<String>()
        var lines: [String] = []
        for e in elements {
            guard let b = e["bounds"] as? [Int], b.count == 4, b[2] > 0, b[3] > 0 else { continue }
            let frame = CGRect(x: b[0], y: b[1], width: b[2], height: b[3])
            let overlap = frame.intersection(region)
            guard !overlap.isNull, overlap.width * overlap.height >= 0.5 * frame.width * frame.height else { continue }
            let role = e["role"] as? String ?? ""
            let label = (e["label"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let value = (e["value"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !label.isEmpty || !value.isEmpty else { continue }
            var line = label
            if !value.isEmpty, value != label { line += label.isEmpty ? value : ": \(value)" }
            if !["text", "group", ""].contains(role) { line = "\(role) \(line)" }
            if seen.insert(line.lowercased()).inserted { lines.append(line) }
        }
        return lines
    }

    /// The combined selection text: OCR first, then any element lines the OCR did not already contain.
    static func text(ocr: String, elementLines: [String], limit: Int = maxChars) -> String {
        let ocr = ocr.trimmingCharacters(in: .whitespacesAndNewlines)
        let lowerOCR = ocr.lowercased()
        let extra = elementLines.filter { line in
            // Skip "button Send" when OCR already read "Send".
            let words = line.split(separator: " ", maxSplits: 1).map(String.init)
            let core = words.count == 2 && !line.contains(":") ? words[1] : line
            return !lowerOCR.contains(core.lowercased()) && !lowerOCR.contains(line.lowercased())
        }
        var parts: [String] = []
        if !ocr.isEmpty { parts.append(ocr) }
        if !extra.isEmpty {
            parts.append((ocr.isEmpty ? "" : "Controls in the selection:\n") + extra.map { "- \($0)" }.joined(separator: "\n"))
        }
        let joined = parts.joined(separator: "\n\n")
        guard joined.count > limit else { return joined }
        return String(joined.prefix(limit - 1)) + "…"
    }

    /// The `focus` payload for `stt.final`, or nil when nothing readable was selected.
    static func payload(ocr: String, elementLines: [String], app: String?, bounds: CGRect,
                        limit: Int = maxChars) -> JSONObject? {
        let text = text(ocr: ocr, elementLines: elementLines, limit: limit)
        guard !text.isEmpty else { return nil }
        var focus: JSONObject = [
            "text": text,
            "source": "selection",
            "bounds": [Int(bounds.minX.rounded()), Int(bounds.minY.rounded()),
                       Int(bounds.width.rounded()), Int(bounds.height.rounded())],
        ]
        if let app, !app.isEmpty { focus["app"] = app }
        return focus
    }

    /// A short spoken/visible preview of what was read.
    static func preview(_ text: String, limit: Int = 220) -> String {
        let flat = text.split(whereSeparator: \.isNewline).joined(separator: " ")
        guard flat.count > limit else { return flat }
        return String(flat.prefix(limit - 1)) + "…"
    }
}

/// Captures, OCRs and reads the accessibility elements of a selected region.
enum RegionReader {
    struct Reading {
        let focus: JSONObject?
        let ocrLineCount: Int
        let elementCount: Int
    }

    /// `region` is a top-left-origin global rect in points.
    static func read(region: CGRect, accessibility: AccessibilityProviding) async throws -> Reading {
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperError(.permissionDenied, "Screen Recording permission is not granted")
        }
        guard let target = RegionGeometry.captureTarget(for: region, displays: RegionGeometry.activeDisplays()) else {
            throw HelperError(.badRequest, "the selection is not on a display")
        }
        async let elementsTask = elements(in: target.global, accessibility: accessibility)
        let image = try await capture(displayID: target.display.id, local: target.local)
        let lines = try await recognize(image)
        let (app, elementLines) = await elementsTask
        let ocr = SelectionFocus.readingOrder(lines)
        let focus = SelectionFocus.payload(ocr: ocr, elementLines: elementLines, app: app, bounds: target.global)
        return Reading(focus: focus, ocrLineCount: lines.count, elementCount: elementLines.count)
    }

    private static func elements(in region: CGRect, accessibility: AccessibilityProviding) async -> (String?, [String]) {
        do {
            let snapshot = try await accessibility.snapshot(scope: "window", maxElements: 400)
            let elements = snapshot["elements"] as? [JSONObject] ?? []
            return (snapshot["app"] as? String, SelectionFocus.elementLines(elements, region: region))
        } catch {
            Log.warn("selection: no accessibility elements (\(error))")
            return (nil, [])
        }
    }

    static func capture(displayID: CGDirectDisplayID, local: CGRect) async throws -> CGImage {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == displayID }) else {
            throw HelperError(.actionFailed, "display \(displayID) is not available to capture")
        }
        let ownPID = ProcessInfo.processInfo.processIdentifier
        let ownWindows = content.windows.filter { $0.owningApplication?.processID == ownPID }
        let filter = SCContentFilter(display: display, excludingWindows: ownWindows)
        let config = SCStreamConfiguration()
        let scale = CGFloat(filter.pointPixelScale)
        config.sourceRect = local
        config.width = max(1, Int(local.width * scale))
        config.height = max(1, Int(local.height * scale))
        config.showsCursor = false
        return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    }

    /// How long accurate OCR may take before the fast recognizer's result is used instead.
    nonisolated(unsafe) static var accurateBudget: TimeInterval = 4
    /// Set for the rest of the run once accurate OCR fails or times out. On the 8 GB demo Mac, with both local
    /// models loaded, the accurate recognizer's Neural Engine model failed to load after ~60 s (e5rt error 13)
    /// and held up other Vision requests meanwhile, while the fast recognizer (CPU) answered in under a second.
    private static let accurateUnavailable = LockedValue(false)

    /// Vision OCR. The fast recognizer runs first (it cannot get stuck behind the accurate one); then the accurate
    /// recognizer with language correction gets up to `accurateBudget`, and its result wins when it arrives.
    static func recognize(_ image: CGImage) async throws -> [SelectionFocus.OCRLine] {
        let fast = await run(image, level: .fast, budget: 15)
        if !accurateUnavailable.get() {
            switch await run(image, level: .accurate, budget: accurateBudget) {
            case let .success(lines)?:
                return lines
            case let .failure(error)?:
                accurateUnavailable.set(true)
                Log.warn("accurate OCR failed (\(error)); using fast OCR from now on")
            case nil:
                accurateUnavailable.set(true)
                Log.warn("accurate OCR took over \(accurateBudget) s; using fast OCR from now on")
            }
        }
        switch fast {
        case let .success(lines)?: return lines
        case let .failure(error)?: throw error
        case nil: throw HelperError(.timeout, "reading the selection took too long")
        }
    }

    /// Runs one recognition request on a background queue. Nil if it did not finish within `budget` seconds.
    private static func run(_ image: CGImage, level: VNRequestTextRecognitionLevel,
                            budget: TimeInterval) async -> Result<[SelectionFocus.OCRLine], Error>? {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = level
        request.usesLanguageCorrection = true
        let gate = OnceResult<Result<[SelectionFocus.OCRLine], Error>?>()
        return await withCheckedContinuation { continuation in
            gate.set(continuation)
            DispatchQueue.global(qos: .userInitiated).async {
                let result: Result<[SelectionFocus.OCRLine], Error>
                do {
                    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                    result = .success((request.results ?? []).compactMap { obs -> SelectionFocus.OCRLine? in
                        guard let best = obs.topCandidates(1).first, best.confidence >= 0.3 else { return nil }
                        return SelectionFocus.OCRLine(text: best.string, box: obs.boundingBox)
                    })
                } catch {
                    result = .failure(error)
                }
                gate.resume(result)
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + budget) {
                if gate.resume(nil) { request.cancel() }
            }
        }
    }
}

/// Resumes a continuation exactly once; later calls return false.
private final class OnceResult<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Never>?
    func set(_ c: CheckedContinuation<T, Never>) { lock.withLock { continuation = c } }
    @discardableResult
    func resume(_ value: T) -> Bool {
        let c = lock.withLock { () -> CheckedContinuation<T, Never>? in
            defer { continuation = nil }
            return continuation
        }
        c?.resume(returning: value)
        return c != nil
    }
}
