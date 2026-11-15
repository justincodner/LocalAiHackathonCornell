// Owner: Mac owner. SwiftUI contents of the floating Beacon HUD.
import AppKit
import SwiftUI

struct PillView: View {
    @ObservedObject var model: HUDModel
    var dragHeader: ((NSEvent) -> Void)?
    var minimize: (() -> Void)?
    var turnOff: (() -> Void)?
    var toggleQuestionModel: (() -> Void)?
    var typePrompt: (() -> Void)?
    var askAboutRegion: (() -> Void)?
    /// The X on a selection thumbnail: drops the screenshot and starts a fresh conversation.
    var clearSelection: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            if model.replyAboveHeader, !model.replyText.isEmpty {
                reply
                separator
            }
            header
            if !model.replyAboveHeader, !model.replyText.isEmpty {
                separator
                reply
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background {
            VStack(spacing: 0) {
                if model.replyAboveHeader { replySurface }
                HUDStyle.header.opacity(model.reduceTransparency ? 1 : 0.94)
                    .frame(height: PillPanel.size.height)
                if !model.replyAboveHeader { replySurface }
            }
        }
        .clipShape(cardShape)
        .overlay {
            cardShape.strokeBorder(isQuestion ? HUDStyle.question : .white.opacity(0.25), lineWidth: isQuestion ? 2 : 1)
                .allowsHitTesting(false)
        }
    }

    /// Asking, listening for the answer to a question, or waiting for a question about a selection.
    private var isQuestion: Bool { model.state == .asking || model.isAnswering || model.showsSelection }

    private var cardShape: RoundedRectangle {
        RoundedRectangle(cornerRadius: 12)
    }

    private var replySurface: some View {
        Color.black.opacity(model.reduceTransparency ? 1 : 0.90)
            .frame(maxHeight: .infinity)
    }

    private var separator: some View {
        Rectangle().fill(.white.opacity(0.20)).frame(height: 1)
    }

    private var header: some View {
        HStack(spacing: 0) {
            headerStatus
                .padding(.leading, 15)
                .padding(.trailing, 6)
                .frame(height: PillPanel.size.height)
                .overlay {
                    if let dragHeader {
                        HeaderDragView(drag: dragHeader).accessibilityHidden(true)
                    }
                }
            if minimize != nil || turnOff != nil || typePrompt != nil || askAboutRegion != nil {
                HStack(spacing: 6) {
                    if let typePrompt {
                        headerButton(symbol: "keyboard", label: "Type a request",
                                     help: "Type a question instead of speaking (\(model.typeShortcut)). Press again to close.",
                                     action: typePrompt)
                    }
                    if let askAboutRegion {
                        headerButton(symbol: "viewfinder", label: "Ask about part of the screen",
                                     help: "Drag around part of the screen, then ask about it (\(model.snapshotShortcut))",
                                     action: askAboutRegion)
                    }
                    if let minimize {
                        headerButton(symbol: "minus", label: "Minimize Beacon",
                                     help: "Hide Beacon. Press \(model.talkKey) and say \"Hey Beacon\" to bring it back.",
                                     action: minimize)
                    }
                    if let turnOff {
                        HUDMenuButton(label: "More options", help: "Turn off Beacon, or switch the 4B model for questions",
                                      items: moreItems(turnOff: turnOff))
                            .frame(width: Self.buttonSize, height: Self.buttonSize)
                            .background(.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 7))
                    }
                }
                .padding(.trailing, 12)
            }
        }
        .foregroundStyle(.white)
    }

    static let buttonSize: CGFloat = 28

    /// The ⋯ menu: the 4B toggle (checked while on) and Turn off.
    private func moreItems(turnOff: @escaping () -> Void) -> [HUDMenuButton.Item] {
        var items: [HUDMenuButton.Item] = []
        if let toggleQuestionModel {
            items.append(.init(title: Self.questionModelTitle(model.questionModel),
                               checked: model.questionModel == .on,
                               enabled: model.questionModel != .loading, action: toggleQuestionModel))
        }
        items.append(.init(title: "Turn off Beacon", checked: false, enabled: true, action: turnOff))
        return items
    }

    static func questionModelTitle(_ state: QuestionModelState,
                                   memoryBytes: UInt64 = ProcessInfo.processInfo.physicalMemory) -> String {
        if state == .loading { return "Loading 4B model…" }
        let note = memoryNote(memoryBytes)
        return "Use 4B model for questions" + (note.isEmpty ? "" : " (\(note))")
    }

    /// "slower on 8 GB" when running both models would swap on this Mac (under 16 GB), otherwise nothing.
    static func memoryNote(_ memoryBytes: UInt64) -> String {
        let gigabytes = Int((Double(memoryBytes) / 1_073_741_824).rounded())
        return gigabytes < 16 ? "slower on \(gigabytes) GB" : ""
    }
    static let removeButtonSize: CGFloat = 24

    private func headerButton(symbol: String, label: String, help: String, action: @escaping () -> Void) -> some View {
        HUDButton(symbol: symbol, label: label, help: help, action: action)
            .frame(width: Self.buttonSize, height: Self.buttonSize)
            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
    }

    private var headerStatus: some View {
        let style = HUDStyle.of(model.state)
        let status = model.statusText
        return HStack(spacing: 12) {
            Image(systemName: model.isAnswering || (model.showsSelection && model.state == .listening) ? "mic.fill"
                  : model.showsSelection ? "viewfinder" : style.symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(isQuestion ? Color.black : Color.white)
                .frame(width: 34, height: 34)
                .background(isQuestion ? AnyShapeStyle(HUDStyle.question) : AnyShapeStyle(.white.opacity(0.12)),
                            in: RoundedRectangle(cornerRadius: 7))
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(verbatim: status.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(isQuestion ? HUDStyle.question : .white)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                    if let word = status.word {
                        Text(verbatim: word)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(HUDStyle.secondaryText)
                    }
                }
                if let detail = status.detail {
                    Text(verbatim: detail)
                        .font(.system(size: 13, weight: status.detailIsHint ? .medium : .regular))
                        .foregroundStyle(status.detailIsHint ? .white : HUDStyle.secondaryText)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(status.accessibilityLabel)

            VStack(alignment: .trailing, spacing: 3) {
                if model.offline {
                    Label("Offline", systemImage: "wifi.slash")
                        .font(.system(size: 11, weight: .semibold))
                }
                if let ms = model.latencyMS {
                    Text("\(ms) ms")
                        .font(.system(size: 11).monospacedDigit())
                        .accessibilityLabel("Last response \(ms) milliseconds")
                }
            }
            .foregroundStyle(HUDStyle.secondaryText)
        }
    }

    private var reply: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                if let label = model.replyLabel {
                    Text(verbatim: label.uppercased())
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(HUDStyle.question)
                        .accessibilityHidden(true)
                }
                if model.showsSelectionThumbnail, let image = model.selectionImage {
                    let size = PillPanel.thumbnailSize(
                        image: image.size, maxWidth: PillPanel.size.width - Self.replyHorizontalPadding * 2)
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.high)
                        .frame(width: size.width, height: size.height)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.white.opacity(0.35), lineWidth: 1))
                        .overlay {
                            if model.selectionLoading {
                                ZStack {
                                    RoundedRectangle(cornerRadius: 6).fill(Color.black.opacity(0.55))
                                    ProgressView()
                                        .progressViewStyle(.circular)
                                        .controlSize(.regular)
                                        .tint(.white)
                                        .colorScheme(.dark)
                                        .frame(width: 48, height: 48)
                                        .background(Circle().fill(Color.black.opacity(0.85)))
                                        .overlay(Circle().strokeBorder(.white.opacity(0.6), lineWidth: 1))
                                }
                                .accessibilityLabel("Reading the selection")
                            }
                        }
                        .overlay(alignment: .topTrailing) {
                            if let clearSelection {
                                HUDButton(symbol: "xmark", label: "Remove screenshot",
                                          help: "Remove this screenshot and start a new conversation",
                                          action: clearSelection)
                                    .frame(width: Self.removeButtonSize, height: Self.removeButtonSize)
                                    .background(Circle().fill(Color.black.opacity(0.85)))
                                    .overlay(Circle().strokeBorder(.white.opacity(0.6), lineWidth: 1))
                                    .padding(5)
                            }
                        }
                        .accessibilityLabel("Your selection: \(model.replyText)")
                } else {
                Text(verbatim: model.replyText)
                    .font(.system(size: isQuestion ? 17 : 16, weight: isQuestion ? .medium : .regular))
                    .lineSpacing(4)
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(model.replyLabel.map { "\($0): \(model.replyText)" }
                                        ?? (model.caption.isEmpty ? model.replyText : model.caption))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Self.replyHorizontalPadding)
            .padding(.vertical, 14)
        }
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .accessibilityLabel(model.showsSelection ? "Your selection" : isQuestion ? "Beacon's question" : "Beacon reply")
    }

    static let replyHorizontalPadding: CGFloat = 16
}

/// Small header button. AppKit-backed so the first click works in the non-activating panel without activating Beacon.
private struct HUDButton: NSViewRepresentable {
    let symbol: String
    let label: String
    let help: String
    let action: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> FirstMouseButton {
        let button = FirstMouseButton()
        button.imagePosition = .imageOnly
        button.contentTintColor = .white
        button.isBordered = false
        button.target = context.coordinator
        button.action = #selector(Coordinator.press(_:))
        return button
    }

    func updateNSView(_ button: FirstMouseButton, context: Context) {
        context.coordinator.action = action
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: label)?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .semibold))
        button.toolTip = help
        button.setAccessibilityLabel(label)
        button.setAccessibilityHelp(help)
    }

    final class FirstMouseButton: NSButton {
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override var mouseDownCanMoveWindow: Bool { false }
    }

    @MainActor
    final class Coordinator: NSObject {
        var action: (() -> Void)?
        @objc func press(_ sender: NSButton) { action?() }
    }
}

/// The ⋯ header button: pops up a native menu. AppKit-backed so the first click works in the non-activating panel.
struct HUDMenuButton: NSViewRepresentable {
    struct Item {
        let title: String
        let checked: Bool
        let enabled: Bool
        let action: () -> Void
    }

    let label: String
    let help: String
    let items: [Item]

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> HUDButtonView {
        let button = HUDButtonView()
        button.imagePosition = .imageOnly
        button.contentTintColor = .white
        button.isBordered = false
        button.target = context.coordinator
        button.action = #selector(Coordinator.press(_:))
        button.image = NSImage(systemSymbolName: "ellipsis", accessibilityDescription: label)?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .semibold))
        return button
    }

    func updateNSView(_ button: HUDButtonView, context: Context) {
        context.coordinator.items = items
        button.toolTip = help
        button.setAccessibilityLabel(label)
        button.setAccessibilityHelp(help)
    }

    final class HUDButtonView: NSButton {
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override var mouseDownCanMoveWindow: Bool { false }
    }

    @MainActor
    final class Coordinator: NSObject {
        var items: [Item] = []

        @objc func press(_ sender: NSButton) {
            let menu = NSMenu()
            menu.autoenablesItems = false
            menu.appearance = NSAppearance(named: .darkAqua)
            for (index, item) in items.enumerated() {
                if index == items.count - 1, items.count > 1 { menu.addItem(.separator()) }
                let entry = NSMenuItem(title: item.title, action: #selector(choose(_:)), keyEquivalent: "")
                entry.target = self
                entry.tag = index
                entry.state = item.checked ? .on : .off
                entry.isEnabled = item.enabled
                menu.addItem(entry)
            }
            menu.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.height + 4), in: sender)
        }

        @objc func choose(_ sender: NSMenuItem) {
            guard items.indices.contains(sender.tag) else { return }
            items[sender.tag].action()
        }
    }
}

/// Handles the header's initial click without activating the panel or blocking reply scrolling.
private struct HeaderDragView: NSViewRepresentable {
    let drag: (NSEvent) -> Void

    func makeNSView(context: Context) -> DragSurface { DragSurface() }
    func updateNSView(_ view: DragSurface, context: Context) { view.drag = drag }

    final class DragSurface: NSView {
        var drag: ((NSEvent) -> Void)?
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override var mouseDownCanMoveWindow: Bool { false }
        override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
        override func mouseDown(with event: NSEvent) { drag?(event) }
        override func mouseDragged(with event: NSEvent) { drag?(event) }
        override func mouseUp(with event: NSEvent) { drag?(event) }
    }
}
