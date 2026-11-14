// Owner: Mac owner. Observable HUD state shared by the pill view and the controller (spec 9.6).
import AppKit
import SwiftUI

@MainActor
final class HUDModel: ObservableObject {
    @Published var state: HUDState = .idle
    @Published var transcript: String = ""
    @Published var caption: String = ""
    @Published var latencyMS: Int?
    @Published var offline: Bool = false
    @Published var reduceMotion: Bool = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion

    /// The line shown in the pill: the caption replaces the transcript when set.
    var displayText: String { caption.isEmpty ? transcript : caption }
    var showingCaption: Bool { !caption.isEmpty }
}

/// Visual identity of each state. Colours are always paired with a word and an icon, never used alone.
/// Every fill is bright enough that black text on it, and the fill on the near-black pill, exceed 7:1.
struct HUDStyle {
    let word: String
    let symbol: String
    let fill: Color

    static let pillBackground = Color(red: 0x14 / 255, green: 0x14 / 255, blue: 0x14 / 255)   // #141414
    static let primaryText = Color.white                                                       // 18.4:1
    static let secondaryText = Color(red: 0xD6 / 255, green: 0xD6 / 255, blue: 0xD6 / 255)     // 12.7:1
    static let ink = Color.black

    static func of(_ state: HUDState) -> HUDStyle {
        switch state {
        case .idle:      return HUDStyle(word: "Ready", symbol: "circle", fill: hex(0xD1D1D6))
        case .listening: return HUDStyle(word: "Listening", symbol: "mic.fill", fill: hex(0x5EE07A))
        case .thinking:  return HUDStyle(word: "Thinking", symbol: "hourglass", fill: hex(0x7CC4FF))
        case .acting:    return HUDStyle(word: "Acting", symbol: "cursorarrow.click.2", fill: hex(0xFFB547))
        case .speaking:  return HUDStyle(word: "Speaking", symbol: "speaker.wave.2.fill", fill: hex(0xD2B4FF))
        case .confirm:   return HUDStyle(word: "Confirm?", symbol: "questionmark.circle.fill", fill: hex(0xFFE14D))
        case .error:     return HUDStyle(word: "Error", symbol: "exclamationmark.triangle.fill", fill: hex(0xFF9A8F))
        }
    }

    private static func hex(_ v: UInt32) -> Color {
        Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }
}
