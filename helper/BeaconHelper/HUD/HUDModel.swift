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
    /// The question Beacon asked (state `asking`). Kept while the user answers, cleared once the answer is in.
    @Published var pendingQuestion: String?
    @Published var reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    @Published var reduceTransparency = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
    /// Name of the push-to-talk key as people say it ("Option").
    var talkKey = HUDStatusText.talkKeyName(Config.pttKey)

    var displayText: String { caption.isEmpty ? transcript : caption }
    var visibleCaption: String { revealedCaption ?? caption }

    /// True while the user is speaking an answer to Beacon's question.
    var isAnswering: Bool { state == .listening && pendingQuestion != nil }

    /// What the reply area shows: the caption, or the pending question while the user answers it.
    var replyText: String {
        if isAnswering, caption.isEmpty, let pendingQuestion { return pendingQuestion }
        return visibleCaption
    }

    /// Small label above the reply text, if any.
    var replyLabel: String? {
        (state == .asking || isAnswering) && !replyText.isEmpty ? "Question" : nil
    }

    var statusText: HUDStatusText {
        HUDStatusText.of(state: state, pendingQuestion: pendingQuestion, transcript: transcript, talkKey: talkKey)
    }
}

/// Header wording for a HUD state. Pure, so the asking and answering labels can be unit tested.
struct HUDStatusText: Equatable {
    /// Bold first word(s) of the header, e.g. "Beacon" or "Beacon is asking".
    let title: String
    /// Short state word beside the title, e.g. "Listening". Nil when the title already says it.
    let word: String?
    /// Second header line: what the user said, or a hint about what to do next.
    let detail: String?
    /// True when `detail` is a hint rather than the user's own words.
    let detailIsHint: Bool

    static func of(state: HUDState, pendingQuestion: String?, transcript: String, talkKey: String = "Option") -> HUDStatusText {
        let said = transcript.isEmpty ? nil : transcript
        switch state {
        case .asking:
            return HUDStatusText(title: "Beacon is asking", word: nil,
                                 detail: "Hold \(talkKey) to answer", detailIsHint: true)
        case .listening where pendingQuestion != nil:
            return HUDStatusText(title: "Answering Beacon's question", word: nil,
                                 detail: said ?? "Say your answer, then release \(talkKey)", detailIsHint: said == nil)
        default:
            return HUDStatusText(title: "Beacon", word: HUDStyle.of(state).word, detail: said, detailIsHint: false)
        }
    }

    /// Spoken name of a `BEACON_PTT_KEY` value.
    static func talkKeyName(_ key: String) -> String {
        switch key.lowercased() {
        case "right_command", "right_cmd": return "Command"
        case "right_control", "right_ctrl": return "Control"
        case "right_shift": return "Shift"
        case "fn", "function": return "Fn"
        default: return "Option"
        }
    }

    /// One VoiceOver label for the whole header.
    var accessibilityLabel: String {
        var parts = [title]
        if let word { parts.append(word) }
        if let detail { parts.append(detailIsHint ? detail : "You said: \(detail)") }
        return parts.joined(separator: ", ")
    }
}

struct HUDStyle {
    let word: String
    let symbol: String

    // Dark translucent surfaces keep even the secondary text readable over white content.
    static let header = Color(white: 0.09)
    static let secondaryText = Color(white: 0.80)
    /// Amber marks a question waiting for the user (asking, and listening for the answer).
    static let question = Color(red: 1.0, green: 0.76, blue: 0.20)

    static func of(_ state: HUDState) -> HUDStyle {
        switch state {
        case .idle:      return HUDStyle(word: "Ready", symbol: "circle")
        case .listening: return HUDStyle(word: "Listening", symbol: "mic.fill")
        case .thinking:  return HUDStyle(word: "Thinking", symbol: "hourglass")
        case .acting:    return HUDStyle(word: "Acting", symbol: "cursorarrow.click.2")
        case .speaking:  return HUDStyle(word: "Speaking", symbol: "speaker.wave.2.fill")
        case .confirm:   return HUDStyle(word: "Confirm?", symbol: "questionmark.circle.fill")
        case .asking:    return HUDStyle(word: "Asking", symbol: "questionmark.bubble.fill")
        case .error:     return HUDStyle(word: "Error", symbol: "exclamationmark.triangle.fill")
        }
    }
}
