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
    /// Short preview of a screen region the user selected (Control+Option+S), while it waits for a question.
    @Published var selectionPreview: String?
    /// The captured region itself, drawn as a small preview instead of the selected text.
    @Published var selectionImage: NSImage?
    /// Name of the push-to-talk key as people say it ("Option").
    var talkKey = HUDStatusText.talkKeyName(Config.pttKey)
    /// Spoken names of the global shortcuts ("Control Option Space", "Control Option S").
    var typeShortcut = GlobalShortcut.typePrompt.spokenName
    var snapshotShortcut = GlobalShortcut.regionSnapshot.spokenName

    var displayText: String { caption.isEmpty ? transcript : caption }
    var visibleCaption: String { revealedCaption ?? caption }

    /// True while a selected region waits for the user's question (it takes over the idle and asking looks).
    var showsSelection: Bool {
        selectionPreview != nil && [.idle, .asking, .listening, .error].contains(state)
    }

    /// True while the reply area shows the selection's thumbnail rather than text.
    var showsSelectionThumbnail: Bool {
        showsSelection && selectionImage != nil && replyText == selectionPreview
    }

    /// True while the user is speaking an answer to Beacon's question.
    var isAnswering: Bool { state == .listening && pendingQuestion != nil && !showsSelection }

    /// What the reply area shows: the caption, the pending question while the user answers it, or the
    /// selected text while it waits for a question.
    var replyText: String {
        if showsSelection, caption.isEmpty, let selectionPreview { return selectionPreview }
        if isAnswering, caption.isEmpty, let pendingQuestion { return pendingQuestion }
        return visibleCaption
    }

    /// Small label above the reply text, if any.
    var replyLabel: String? {
        guard !replyText.isEmpty else { return nil }
        if showsSelection, replyText == selectionPreview { return "Selection" }
        return state == .asking || isAnswering ? "Question" : nil
    }

    var statusText: HUDStatusText {
        HUDStatusText.of(state: state, pendingQuestion: pendingQuestion, transcript: transcript, talkKey: talkKey,
                         selection: showsSelection)
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

    static func of(state: HUDState, pendingQuestion: String?, transcript: String, talkKey: String = "Option",
                   selection: Bool = false) -> HUDStatusText {
        let said = transcript.isEmpty ? nil : transcript
        if selection {
            if state == .listening {
                return HUDStatusText(title: "Asking about your selection", word: nil,
                                     detail: said ?? "Say your question, then release \(talkKey)",
                                     detailIsHint: said == nil)
            }
            return HUDStatusText(title: "Ask about this selection", word: nil,
                                 detail: "Hold \(talkKey) to ask, or type it",
                                 detailIsHint: true)
        }
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
    /// A question waiting for the user (asking, and listening for the answer) stands out in solid white,
    /// keeping the HUD monochrome.
    static let question = Color.white

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
