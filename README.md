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

## Windows quick start (Brain)

Windows development: run `./scripts/setup_windows.ps1`, start Bonsai with `./scripts/start_bonsai.ps1`, then run `uv run --project brain python -m mock_helper` and `uv run --project brain python -m beacon_brain` in separate terminals from the repo root. See [evaluations](evals/README.md) for fixture and scoring commands.
Brain status and Windows run commands (no uv needed): [docs/BRAIN_HANDOFF.md](docs/BRAIN_HANDOFF.md).
