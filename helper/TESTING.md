# Beacon Helper: manual test checklist

Owner: Mac owner. Covers what `scripts/contract_test.sh` cannot check automatically (spec 14.1). Run it against the signed app in `/Applications` (`scripts/build_helper.sh --install`), never a debug binary, because macOS ties permissions to the signature.

Record date, build (git short hash) and result at the bottom each time you run it.

## 1. Build and signature

- [ ] `scripts/build_helper.sh --install` finishes with no error.
- [ ] `codesign -dv "/Applications/Beacon Helper.app"` shows `Identifier=dev.beacon.helper`, an `Apple Development` authority and `flags=0x10000(runtime)`.
- [ ] `codesign -d --entitlements - "/Applications/Beacon Helper.app"` lists `com.apple.security.device.audio-input` and app-sandbox `false`.
- [ ] No Dock icon appears when the app runs (LSUIElement); only the menu-bar item.

## 2. Permission prompts (first launch)

Reset first to test a clean machine: `tccutil reset Microphone dev.beacon.helper; tccutil reset Accessibility dev.beacon.helper`.

- [ ] First launch asks for Microphone with the text from `NSMicrophoneUsageDescription`.
- [ ] First launch opens the Accessibility prompt (System Settings > Privacy & Security > Accessibility lists "Beacon Helper").
- [ ] Screen Recording is **not** requested unless P2 vision is built.
- [ ] After granting, `curl -s 127.0.0.1:8765/health | jq .permissions` shows `accessibility: true, microphone: true`.
- [ ] Rebuild with `scripts/build_helper.sh --install` and relaunch: **no** re-prompt, and `/health` still shows both `true` (stable signing identity).
- [ ] Revoke Accessibility in System Settings: `/ax/snapshot` returns `{"ok": false, "error": {"code": "permission_denied", ...}}` rather than crashing.

## 3. HUD never takes focus

- [ ] With TextEdit frontmost and the cursor in a document, cycle every state: `for s in listening thinking acting speaking confirm error idle; do curl -s -XPOST 127.0.0.1:8765/hud -d "{\"state\":\"$s\",\"caption\":\"$s\"}"; sleep 1; done`. Typing continues to go into TextEdit the whole time.
- [ ] The frontmost app (menu bar app name) never changes to Beacon Helper.
- [ ] `/ax/snapshot` taken while the HUD is visible reports the target app, never "Beacon Helper".
- [ ] The highlight ring (`/hud` with `highlight`) draws around the right element and does not intercept clicks.
- [ ] HUD shows the Offline badge when Wi-Fi is off and hides it when online.
- [ ] HUD is readable on the demo display scaling; it stays on the screen with the target window.

## 4. Talk key and speech in

- [ ] Holding the talk key (default right Option) plays the listen sound and emits `listen.started` (watch with `websocat ws://127.0.0.1:8765/events` or any WebSocket client).
- [ ] Releasing after a 2 to 5 second sentence emits `stt.final` with the correct text; `stt_ms` is 500 or less with the default model.
- [ ] A tap under 300 ms emits `listen.cancelled` and plays the error tone.
- [ ] Holding the key with the room silent gives `listen.cancelled` (empty transcript), not an `stt.final`.
- [ ] Holding right Option does not type characters into the frontmost app.

## 5. Speech out and barge-in timing

- [ ] `curl -s -XPOST 127.0.0.1:8765/tts/speak -d '{"text":"Hello from Beacon."}'` speaks; `tts.started` arrives within 500 ms.
- [ ] `priority: "interrupt"` flushes anything queued.
- [ ] Start a long read (`demo/article.html`, or a long `/tts/speak`), then `time curl -s -XPOST 127.0.0.1:8765/tts/stop`: audio is silent within 100 ms (judge by ear and the `tts.finished` event with `interrupted: true`).
- [ ] Barge-in: during a long read, press the talk key. Audio stops within 100 ms, a `barge_in` event is emitted, and the microphone opens for the new request.
- [ ] Repeat barge-in five times in a row: no stuck audio, no crash, no overlapping voices.
- [ ] `BEACON_TTS=system` fallback speaks with `AVSpeechSynthesizer`, and `/health` shows `tts_engine: "system"`.

## 6. Accessibility and actions (spot checks)

- [ ] `scripts/dump_fixture.sh mail_inbox` (switch to Mail within 3 s) writes a fixture that passes `scripts/contract_test.sh --fixtures-only`.
- [ ] Snapshot in Mail builds in 300 ms or less (`build_ms`).
- [ ] Safari `demo/checkout.html`: the pay button appears as `button "unlabeled, near 'Order total'"` (inferred).
- [ ] `scripts/contract_test.sh --live` passes (opens TextEdit, types, scrolls).
- [ ] In Mail's local Beacon Demo mailbox, select Sarah Chen's "Lunch tomorrow?" and close any open drafts. Run `scripts/mail_reply_test.sh`, switch to Mail during the countdown, and keep Mail frontmost with the keyboard and mouse idle. The script verifies text in the open, unsent reply.

## 7. Offline checklist (spec 11.5)

- [ ] Turn Wi-Fi off, unplug Ethernet, and restart the Mac.
- [ ] `scripts/run_all.sh` starts all three processes with no error.
- [ ] `GET /health` reports `online: false` and all required permissions granted.
- [ ] The HUD shows the Offline badge.
- [ ] Run the full demo script (`demo/DEMO_SCRIPT.md`) twice without touching the network.
- [ ] Search the logs for download or connection errors: `grep -riE "download|connection|unreachable|offline" logs/` shows none that matter.
- [ ] `grep -rE "https?://" brain/ helper/ --include=*.py --include=*.swift` shows only loopback addresses (and the SwiftPM dependency URLs in `Package.swift`, which is not matched by the include filter).

## Results log

| Date | Build | Tester | Result / notes |
| --- | --- | --- | --- |
| 2026-11-14 | `cb4a253` | Codex | Signed workspace build reported Accessibility and Microphone granted. Live contract suite passed 49 checks after TextEdit finished launching. P2 capture skipped. Mail Reply verified typed text in the unsent seed draft. Mail inbox snapshot took 296 ms, and `/ax/read` returned the full body. |
