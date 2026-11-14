// Owner: Mac owner. SwiftUI contents of the floating Beacon HUD.
import AppKit
import SwiftUI

struct PillView: View {
    @ObservedObject var model: HUDModel
    var dragHeader: ((NSEvent) -> Void)?
    var selectPosition: ((HUDPlacement) -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            if model.replyAboveHeader, !model.visibleCaption.isEmpty {
                reply
                separator
            }
            header
            if !model.replyAboveHeader, !model.visibleCaption.isEmpty {
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
        .overlay { cardShape.strokeBorder(.white.opacity(0.25), lineWidth: 1).allowsHitTesting(false) }
    }

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
            if let selectPosition {
                PositionMenuButton(select: selectPosition)
                    .frame(width: 34, height: 34)
                    .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
                    .padding(.trailing, 12)
            }
        }
        .foregroundStyle(.white)
    }

    private var headerStatus: some View {
        let style = HUDStyle.of(model.state)
        return HStack(spacing: 12) {
            Image(systemName: style.symbol)
                .font(.system(size: 15, weight: .semibold))
                .frame(width: 34, height: 34)
                .background(.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 7))
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text("Beacon").font(.system(size: 15, weight: .semibold))
                    Text(style.word)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(HUDStyle.secondaryText)
                }
                if !model.transcript.isEmpty {
                    Text(verbatim: model.transcript)
                        .font(.system(size: 13))
                        .foregroundStyle(HUDStyle.secondaryText)
                        .lineLimit(1)
                        .truncationMode(.head)
                        .accessibilityLabel("You said: \(model.transcript)")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

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
            Text(verbatim: model.visibleCaption)
                .font(.system(size: 16))
                .lineSpacing(4)
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Self.replyHorizontalPadding)
                .padding(.vertical, 14)
                .accessibilityLabel(model.caption)
        }
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .accessibilityLabel("Beacon reply")
    }

    static let replyHorizontalPadding: CGFloat = 16
}

private struct PositionMenuButton: NSViewRepresentable {
    let select: (HUDPlacement) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> MenuButton {
        let button = MenuButton()
        button.image = NSImage(systemSymbolName: "ellipsis", accessibilityDescription: nil)
        button.imagePosition = .imageOnly
        button.contentTintColor = .white
        button.isBordered = false
        button.toolTip = "Move Beacon"
        button.setAccessibilityLabel("Move Beacon")
        button.target = context.coordinator
        button.action = #selector(Coordinator.openMenu(_:))
        return button
    }

    func updateNSView(_ button: MenuButton, context: Context) {
        context.coordinator.select = select
    }

    final class MenuButton: NSButton {
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    }

    @MainActor
    final class Coordinator: NSObject {
        var select: ((HUDPlacement) -> Void)?

        @objc func openMenu(_ button: NSButton) {
            let menu = NSMenu()
            menu.autoenablesItems = false
            for placement in HUDPlacement.allCases {
                let item = NSMenuItem(title: placement.rawValue.capitalized,
                                      action: #selector(move(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = placement.rawValue
                menu.addItem(item)
            }
            menu.popUp(positioning: nil, at: NSPoint(x: button.bounds.minX, y: button.bounds.minY), in: button)
        }

        @objc func move(_ item: NSMenuItem) {
            guard let value = item.representedObject as? String, let placement = HUDPlacement(rawValue: value)
            else { return }
            select?(placement)
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
