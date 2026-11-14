# Beacon demo script: rehearsal checklist

Owner: Mac owner. Source: spec sections 14.3 and 14.4. Three minutes. Rehearse at least five times; the X2 bar is three clean runs in a row.

## Setup (30 minutes before)

- [ ] Mac on power, Low Power Mode off, Do Not Disturb on.
- [ ] Quit everything except Mail, Notes, Safari and one Terminal. Quit Xcode (it can take several GB on the 8 GB Mac).
- [ ] Display scaled so the HUD is readable from the back of the room.
- [ ] Helper is the signed `/Applications/Beacon Helper.app` (`scripts/build_helper.sh --install`), not a debug build.
- [ ] Mail: seeded local mailbox open on the Inbox, with a message from Sarah Chen near the top.
- [ ] Notes: open, any note list.
- [ ] Safari: open `demo/checkout.html` (File > Open File...). Also have `demo/article.html` in a second tab for step 7.
- [ ] Wi-Fi off and Ethernet unplugged; the Wi-Fi icon in the menu bar shows off.
- [ ] Terminal 1: `scripts/run_all.sh` (Bonsai, Helper, Brain). Wait for "Beacon is running".
- [ ] `curl -s 127.0.0.1:8765/health | jq` shows `online: false` and accessibility + microphone `true`.
- [ ] HUD shows the Offline badge.
- [ ] Terminal 2: `tail -f logs/session.jsonl` (for latency questions).
- [ ] Activity Monitor memory pressure is green. If yellow: `BEACON_TTS=system` and restart the Helper.
- [ ] Warm-up: one "What's on my screen?" before the judges arrive, so caches are hot.
- [ ] Switch back to Mail, Inbox selected.

## Script

| Step | Say or do | Expected result | Shows | Pass |
| --- | --- | --- | --- | --- |
| 1 | Point at the Wi-Fi icon and the Offline badge | Nothing; sets the frame | Fully local | [ ] |
| 2 | "What's on my screen?" | Spoken summary of the Mail inbox, with captions | Screen understanding, not linear reading | [ ] |
| 3 | "Read the message from Sarah" | Highlight ring on the row, then the message read aloud | Targeted reading | [ ] |
| 4 | "Reply that I'll be ten minutes late" | Narrated steps: opens the reply, types, reads the draft back, asks for confirmation | Multi-step agent with narration | [ ] |
| 5 | "Send it" | Sends; "Sent to Sarah Chen." The message waits in the Outbox, since the Mac is offline | Confirmation guard | [ ] |
| 6 | Switch to Safari (`demo/checkout.html`). "Where do I pay?" | Ring on the unlabelled button; "Bottom right. The button has no label; it is next to Order total." | Label repair on an inaccessible page | [ ] |
| 7 | Switch to the `demo/article.html` tab. "Read the whole page", then press the talk key mid-sentence | Speech stops instantly | Barge-in | [ ] |
| 8 | "Delete all my emails" | Asks for confirmation; say "no"; "Okay, cancelled." | Safety | [ ] |

## After each rehearsal

- [ ] Note any step that failed and the latency from `logs/session.jsonl`.
- [ ] Delete the sent reply from Mail's Outbox/Sent so the next run starts from the same state.
- [ ] Reload `demo/checkout.html` (the pay button shows "Order placed" if it was pressed).

## Backup

- [ ] Record a screen capture of one clean run (with audio) and keep it on the desktop in case of a live failure.
- Fallback demo if multi-step is unreliable: steps 1 to 3, 6, 7 and 8 (read path plus single actions).

## Demo pages

- `demo/checkout.html`: labelled checkout form; the pay button at the bottom right next to "Order total" is icon-only with no text, `aria-label` or `title` (on purpose; do not fix it).
- `demo/form.html`: fully labelled volunteer form with a Submit button (fixture `safari_form`).
- `demo/article.html`: long article for "read the whole page" and barge-in.

All three load no network resources, so they work with Wi-Fi off.
