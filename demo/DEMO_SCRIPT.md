# Beacon demo script: rehearsal checklist

Owner: Mac owner. Source: spec sections 14.3 and 14.4, cut down to **two minutes**. Rehearse at least five times; the X2 bar is three clean runs in a row. Time every rehearsal with a stopwatch.

## Setup (30 minutes before)

- [ ] Mac on power, Low Power Mode off, Do Not Disturb on.
- [ ] Quit everything except Mail, Safari and one Terminal. Quit Xcode (it can take several GB on the 8 GB Mac).
- [ ] Display scaled so the HUD is readable from the back of the room.
- [ ] Helper is the signed `/Applications/Beacon Helper.app` (`scripts/build_helper.sh --install`), not a debug build.
- [ ] Mail: seeded local mailbox open on the Inbox, with a message from Sarah Chen near the top.
- [ ] Safari: open `demo/checkout.html` (File > Open File...) in the first tab and `demo/article.html` in the second tab, with the checkout tab in front.
- [ ] Wi-Fi off and Ethernet unplugged; the Wi-Fi icon in the menu bar shows off.
- [ ] Terminal 1: `BEACON_TTS=system scripts/run_all.sh` (Bonsai, Helper, Brain). System speech starts in about 0.3 s; TTSKit takes several seconds per utterance on the M1, which a two-minute slot cannot afford. Wait for "Beacon is running".
- [ ] `curl -s 127.0.0.1:8765/health | jq` shows `online: false` and accessibility + microphone `true`.
- [ ] HUD shows the Offline badge.
- [ ] Terminal 2: `tail -f logs/session.jsonl` (for latency questions).
- [ ] Activity Monitor memory pressure is green.
- [ ] Warm-up: one "What's on my screen?" before the judges arrive, so caches are hot.
- [ ] Switch back to Mail, Inbox selected.

## Script (2:00)

The order keeps every Mail step together, so there is only one app switch. The times in brackets are the time each step should be finished by.

| Step | By | Say or do | Expected result | Shows | Pass |
| --- | --- | --- | --- | --- | --- |
| 1 | 0:10 | Point at the Wi-Fi icon and the Offline badge. Say the pitch line (below). | Nothing; sets the frame | Fully local | [ ] |
| 2 | 0:25 | "What's on my screen?" | Spoken summary of the Mail inbox, with captions | Screen understanding, not linear reading | [ ] |
| 3 | 0:55 | "Reply to Sarah that I'll be ten minutes late" | Narrated steps: opens the reply, types, reads the draft back, asks for confirmation | Multi-step agent with narration | [ ] |
| 4 | 1:05 | "Send it" | Sends; "Sent to Sarah Chen." The message waits in the Outbox, since the Mac is offline | Confirmation guard | [ ] |
| 5 | 1:20 | "Delete all my emails", then "no" | Asks for confirmation; "Okay, cancelled." | Safety rules in code, not trust in the model | [ ] |
| 6 | 1:35 | Switch to Safari (`demo/checkout.html`). "Where do I pay?" | Ring on the unlabelled button; "Bottom right. The button has no label; it is next to Order total." | Works on inaccessible pages | [ ] |
| 7 | 1:50 | Switch to the `demo/article.html` tab. "Read the whole page", then press the talk key after one sentence | Speech stops instantly | Barge-in | [ ] |
| 8 | 2:00 | Closing line (below) | — | — | [ ] |

### Lines to say

- **Pitch (step 1):** "Beacon lets a blind or motor-impaired user run their Mac by voice. Everything runs on this M1 with Wi-Fi off: Bonsai 2B for reasoning, Argmax WhisperKit for speech."
- **While Bonsai thinks (steps 2 and 3):** screen summaries take 5–7 s, so fill the gap: "It's reading the accessibility tree, the same data a screen reader uses, and deciding what to do."
- **Step 5:** "A 2B model can make mistakes, so sending, deleting and paying always need a spoken yes, enforced in code. Passwords and system settings are refused outright."
- **Step 6:** "This button has no label at all. A screen reader would just say 'button'. Beacon works it out from what's around it."
- **Closing (step 8):** "Intent-level voice control, fully offline, with guardrails the model can't talk its way around."

### Numbers for questions

- 30-scenario eval suite: 26/30 on the 2B (16/30 with prompt v1), 29/30 with questions routed to the 4B. A prompt-injection email in the seeded mailbox has its own eval case, and it passes.
- Single actions take about 2 s on the M1 (model picks the control, Helper presses it in about 40 ms).
- The 4B processes a 3,000-token screen in about 17 s on the M1, so the 2B is the default, with a cached system prompt and capped snapshots.
- Stop, repeat, yes/no and "forget that" skip the model entirely.

### Extras for questions (not in the two minutes)

- Select the message from Dana Ortiz (the seeded prompt-injection email) and say "Read this message": Beacon should read the text aloud without following its instructions (eval case `mail_injection`). Rehearse this once before offering it live.
- `demo/poker.html`: "Fold", "Call $4" and "Raise to $11" are plain divs that only respond to pointer events; say "Click fold" and Beacon still presses it.

## After each rehearsal

- [ ] Note the stopwatch time, any step that failed, and the latency from `logs/session.jsonl`.
- [ ] Delete the sent reply from Mail's Outbox/Sent so the next run starts from the same state.
- [ ] Reload `demo/checkout.html` (the pay button shows "Order placed" if it was pressed) and bring that tab to the front.

## Backup

- [ ] Record a screen capture of one clean run (with audio) and keep it on the desktop in case of a live failure.
- If time is short, cut step 7 and say "Press the talk key and it stops speaking instantly" instead.
- If multi-step is unreliable, replace steps 3 and 4 with "Read the message from Sarah" (highlight ring on the row, then the message read aloud).

## Demo pages

- `demo/checkout.html`: labelled checkout form; the pay button at the bottom right next to "Order total" is icon-only with no text, `aria-label` or `title` (on purpose; do not fix it).
- `demo/article.html`: long article for "read the whole page" and barge-in.
- `demo/form.html`: fully labelled volunteer form with a Submit button (fixture `safari_form`). Not used in the two-minute run.
- `demo/poker.html`: pointer-only action buttons, for questions.

All of these load no network resources, so they work with Wi-Fi off.
