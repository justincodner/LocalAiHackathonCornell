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

## Windows quick start (Brain)

Windows development: run `./scripts/setup_windows.ps1`, start Bonsai with `./scripts/start_bonsai.ps1`, then run `uv run --project brain python -m mock_helper` and `uv run --project brain python -m beacon_brain` in separate terminals from the repo root. See [evaluations](evals/README.md) for fixture and scoring commands.
Brain status and Windows run commands (no uv needed): [docs/BRAIN_HANDOFF.md](docs/BRAIN_HANDOFF.md).
