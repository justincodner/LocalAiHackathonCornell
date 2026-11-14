// Owner: Mac owner. P2: POST /screen/capture. Captures the main display (optionally cropped) as base64 PNG.
import AppKit
import Foundation
import ScreenCaptureKit

enum ScreenCapture {
    static func pngBase64(rect: CGRect?) async throws -> String {
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperError(.permissionDenied, "Screen Recording permission is not granted")
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) ?? content.displays.first else {
            throw HelperError(.actionFailed, "no display to capture")
        }
        let ownPID = ProcessInfo.processInfo.processIdentifier
        let ownWindows = content.windows.filter { $0.owningApplication?.processID == ownPID }
        let filter = SCContentFilter(display: display, excludingWindows: ownWindows)
        let config = SCStreamConfiguration()
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        if let rect {
            config.sourceRect = rect
            config.width = Int(rect.width * scale)
            config.height = Int(rect.height * scale)
        } else {
            config.width = Int(CGFloat(display.width) * scale)
            config.height = Int(CGFloat(display.height) * scale)
        }
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        let rep = NSBitmapImageRep(cgImage: image)
        guard let png = rep.representation(using: .png, properties: [:]) else {
            throw HelperError(.actionFailed, "PNG encoding failed")
        }
        return png.base64EncodedString()
    }
}
