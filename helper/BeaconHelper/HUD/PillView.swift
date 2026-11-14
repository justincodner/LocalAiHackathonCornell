// Owner: Mac owner. SwiftUI contents of the HUD pill: state chip, transcript/caption, latency, Offline badge.
import SwiftUI

struct PillView: View {
    @ObservedObject var model: HUDModel

    var body: some View {
        let style = HUDStyle.of(model.state)
        HStack(spacing: 12) {
            // State icon and word on a bright chip with black text (colour is never the only signal).
            HStack(spacing: 6) {
                Image(systemName: style.symbol)
                    .font(.system(size: 15, weight: .bold))
                Text(style.word)
                    .font(.system(size: 15, weight: .bold))
                    .fixedSize()
            }
            .foregroundColor(HUDStyle.ink)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Capsule().fill(style.fill))
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Beacon state: \(style.word)")

            Text(model.displayText.isEmpty ? " " : model.displayText)
                .font(.system(size: 20, weight: model.showingCaption ? .regular : .medium))
                .foregroundColor(HUDStyle.primaryText)
                .lineLimit(2)
                .truncationMode(model.showingCaption ? .tail : .head)   // live transcript keeps the newest words
                .minimumScaleFactor(0.9)
                .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .trailing, spacing: 4) {
                if model.offline {
                    HStack(spacing: 4) {
                        Image(systemName: "wifi.slash").font(.system(size: 11, weight: .bold))
                        Text("Offline").font(.system(size: 12, weight: .heavy))
                    }
                    .foregroundColor(HUDStyle.ink)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Color.white))
                    .accessibilityLabel("Offline")
                }
                if let ms = model.latencyMS {
                    Text(verbatim: "\(ms) ms")
                        .font(.system(size: 13, weight: .semibold).monospacedDigit())
                        .foregroundColor(HUDStyle.secondaryText)
                        .fixedSize()
                        .accessibilityLabel("Last response \(ms) milliseconds")
                }
            }
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 36, style: .continuous)
                .fill(HUDStyle.pillBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: 36, style: .continuous)
                        .strokeBorder(style.fill, lineWidth: 2)
                )
        )
        .animation(model.reduceMotion ? nil : .easeInOut(duration: 0.15), value: model.state)
    }
}
