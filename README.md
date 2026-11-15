# LocalAiHackathonCornell
Our project for the Local AI Hackathon at cornell

## Mac quick start

Run once while online to prepare Bonsai 2 2B and install the Brain's Python dependencies.

```bash
scripts/setup_mac.sh
scripts/build_helper.sh              # signed "Beacon Helper.app" in build/
```

Start the local stack and wait for `listening for voice events` in `logs/brain.log`.

```bash
BEACON_TTS=system scripts/run_all.sh
```

Grant Microphone and Accessibility to Beacon Helper on first launch. Hold Right Option, ask “What’s on my screen?”, then release to hear and see the reply. Demo pages and the rehearsal checklist are in `demo/`. See `helper/TESTING.md` for manual checks and `scripts/contract_test.sh` for endpoint verification.

## Windows: full local Beacon

Beacon also runs on Windows against the real desktop, with a Python Helper that uses UI Automation, Windows voices
and offline Whisper. See [win_helper/README.md](win_helper/README.md).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_helper.ps1   # once, online
powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1            # Bonsai + Windows Helper + Brain
```

Hold Right Alt and speak, or type in the Beacon window. Stop it with `scripts\stop_windows.ps1`.

## Reading a page

Say “read this page” to hear the text in passages, then “continue reading” to move on. Ask questions such as
“What is the renewal date on this page?” or “Find the cancellation terms on this page.” Beacon can search
the extracted text, read more passages, and scroll the active page to investigate.

Both Helpers read the native accessibility tree and fall back to local OCR when there is no usable document
text. OCR covers the active window or browser viewport and needs Screen Recording permission on macOS or an
installed OCR language on Windows.

For direct DOM extraction on macOS with Safari, Chrome, Chromium, Edge, or Brave, set `BEACON_BROWSER_DOM=1`
and enable the browser's **Allow JavaScript from Apple Events** developer setting. Rebuild the signed Helper
and allow the macOS Automation prompt if it appears.

## Windows quick start (Brain)

Windows development: run `./scripts/setup_windows.ps1`, start Bonsai with `./scripts/start_bonsai.ps1`, then run `uv run --project brain python -m mock_helper` and `uv run --project brain python -m beacon_brain` in separate terminals from the repo root. See [evaluations](evals/README.md) for fixture and scoring commands.
Brain status and Windows run commands (no uv needed): [docs/BRAIN_HANDOFF.md](docs/BRAIN_HANDOFF.md).
