// Owner: Mac owner. SwiftUI contents of the floating Beacon HUD.
import AppKit
import SwiftUI

struct PillView: View {
    @ObservedObject var model: HUDModel
    var dragHeader: ((NSEvent) -> Void)?
    var minimize: (() -> Void)?
    var turnOff: (() -> Void)?

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

    /// Asking, or listening for the answer to a question.
    private var isQuestion: Bool { model.state == .asking || model.isAnswering }

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
            if minimize != nil || turnOff != nil {
                HStack(spacing: 6) {
                    if let minimize {
                        HUDButton(symbol: "minus", label: "Minimize Beacon",
                                  help: "Hide Beacon. Press \(model.talkKey) and say \"Hey Beacon\" to bring it back.",
                                  action: minimize)
                            .frame(width: Self.buttonSize, height: Self.buttonSize)
                            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
                    }
                    if let turnOff {
                        HUDButton(symbol: "power", label: "Turn off Beacon", help: "Turn off Beacon (quits Beacon Helper)",
                                  action: turnOff)
                            .frame(width: Self.buttonSize, height: Self.buttonSize)
                            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
                    }
                }
                .padding(.trailing, 12)
            }
        }
        .foregroundStyle(.white)
    }

    static let buttonSize: CGFloat = 28

    private var headerStatus: some View {
        let style = HUDStyle.of(model.state)
        let status = model.statusText
        return HStack(spacing: 12) {
            Image(systemName: model.isAnswering ? "mic.fill" : style.symbol)
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
                Text(verbatim: model.replyText)
                    .font(.system(size: isQuestion ? 17 : 16, weight: isQuestion ? .medium : .regular))
                    .lineSpacing(4)
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(model.replyLabel.map { "\($0): \(model.replyText)" }
                                        ?? (model.caption.isEmpty ? model.replyText : model.caption))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Self.replyHorizontalPadding)
            .padding(.vertical, 14)
        }
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .accessibilityLabel(isQuestion ? "Beacon's question" : "Beacon reply")
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
