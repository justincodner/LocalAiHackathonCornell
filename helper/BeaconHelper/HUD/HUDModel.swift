// Owner: Mac owner. Observable state for the floating Beacon HUD.
import AppKit
import SwiftUI

@MainActor
final class HUDModel: ObservableObject {
    @Published var state: HUDState = .idle
    @Published var transcript = ""
    @Published var caption = ""
    @Published var revealedCaption: String?
    @Published var replyAboveHeader = false
    @Published var latencyMS: Int?
    @Published var offline = false
    @Published var reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    @Published var reduceTransparency = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency

    var displayText: String { caption.isEmpty ? transcript : caption }
    var visibleCaption: String { revealedCaption ?? caption }
}

struct HUDStyle {
    let word: String
    let symbol: String

    // Dark translucent surfaces keep even the secondary text readable over white content.
    static let header = Color(white: 0.09)
    static let secondaryText = Color(white: 0.80)

    static func of(_ state: HUDState) -> HUDStyle {
        switch state {
        case .idle:      return HUDStyle(word: "Ready", symbol: "circle")
        case .listening: return HUDStyle(word: "Listening", symbol: "mic.fill")
        case .thinking:  return HUDStyle(word: "Thinking", symbol: "hourglass")
        case .acting:    return HUDStyle(word: "Acting", symbol: "cursorarrow.click.2")
        case .speaking:  return HUDStyle(word: "Speaking", symbol: "speaker.wave.2.fill")
        case .confirm:   return HUDStyle(word: "Confirm?", symbol: "questionmark.circle.fill")
        case .error:     return HUDStyle(word: "Error", symbol: "exclamationmark.triangle.fill")
        }
    }
}
