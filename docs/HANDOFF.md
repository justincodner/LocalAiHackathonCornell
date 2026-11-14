# Mac owner handoff (Beacon Helper)

Read this first if you are an AI agent picking up the Mac owner's work. The full design is in `hackthonspec.md`
(sections 4, 5, 9, 11, 12 cover the Helper). Keep this file current: update the status table and "Next steps"
whenever you finish or start something, and commit it.

Last updated 2026-11-14.

## Ground rules

- Work on branch `claude/project-thread-nlbiso`; never push to `main`. Open PRs per the project's attribution rules.
- Mac owner owns `helper/`, `contracts/fixtures/`, `scripts/*.sh`, `demo/`. **Emmet (Windows) owns `brain/`,
  `mock_helper/`, `evals/`, `scripts/*.ps1`, `scripts/bench.py` — never edit those.** `contracts/` changes need both people.
- Demo Mac: M1, 8 GB RAM, macOS 27.0.1, Xcode 27.2, Swift 6.4. Disk is tight (about 16 GB free after clearing
  Xcode caches); check `df -h ~` before big downloads.
- Ask Justin before anything needing a login, API key or account, and before destructive deletes.

## Architecture in one paragraph

`helper/` is a SwiftPM package (no Xcode project). One executable target `BeaconHelper` (product `beacon-helper`)
in Swift 5 language mode. Deps: FlyingFox 0.27 (HTTP + WebSocket), argmax-oss-swift 1.1.1 (WhisperKit, TTSKit).
`App/Services.swift` defines one protocol per module; `Server/Routes.swift` implements every endpoint of
`contracts/openapi.yaml` purely against those protocols; `App/AppDelegate.swift` wires concrete types, the push-to-talk
key (`Input/HotkeyMonitor.swift`, Right Option by default), barge-in, `app.changed` and HUD mirroring of events.

| Folder | Implements | Workstream |
| --- | --- | --- |
| `App/` | entry (`main.swift`, incl. `dump` command), config, permissions, connectivity, screen capture, logging | H1 |
| `Server/` | FlyingFox routes, error envelope, `/events` WebSocket hub | H1 |
| `Accessibility/` | `AccessibilityService`: snapshot walker, filters, element table, read/find/act | H2, H3 |
| `Input/` | `InputService`: typing, key chords, scroll, app open/list; `HotkeyMonitor` | H3 |
| `Speech/` | `SpeechInputService` (WhisperKit), `SpeechOutputService` (TTSKit / system voice) | H4, H5 |
| `HUD/` | `HUDController`: pill, highlight ring, sounds, thinking tick | H6 |

## Status

| Item | Status |
| --- | --- |
| Repo hygiene (`.gitattributes`, `.gitignore`, `.env.example`) | Done |
| H1 Helper shell, server, `/health`, `/events`, `/debug/utterance` | Implemented on this branch. Debug build passed. |
| Contracts v1 draft (`contracts/`) | Done; needs Emmet's approval via PR (C0) |
| H6 HUD, ring, sounds | Unit tests passed, including the highlight test in the Mac desktop session. Live walkthrough pending. |
| H2/H3 accessibility + input | Implemented. Mail Reply acceptance script added and mock-tested. Live acceptance pending. |
| H4/H5 speech in/out + model downloads | Local speech benchmarks in [bench.md](bench.md). System speech met both timing targets. Live push-to-talk and audible barge-in pending. |
| Scripts, app packaging, demo pages, Bonsai setup | Done. Signed app installed at `/Applications/Beacon Helper.app`; contract tests 24 pass / 2 fail (AX permission) / 4 skip |
| Fixtures | `safari_form` and `safari_form_unlabeled` validate. Remaining captures depend on Accessibility. |
| P2 vision label repair (S2) | Not started |

## How to build, test, run

```bash
cd helper && swift build && swift test          # debug build + unit tests
scripts/build_helper.sh --install                # signed "Beacon Helper.app" (stable identity keeps permissions)
scripts/run_all.sh                               # Bonsai + Helper + Brain (Brain only if brain/ exists)
scripts/contract_test.sh                         # endpoint shape checks; --live also clicks/types
scripts/mail_reply_test.sh                       # select Sarah's seeded message, then verify typing into a reply draft
scripts/dump_fixture.sh mail_inbox 3             # record a fixture after a 3 s delay
curl -s localhost:8765/health
curl -s -X POST localhost:8765/debug/utterance -d '{"text":"what is on my screen"}'
```

Models live in git-ignored `models/` (`models/whisperkit-coreml/<model>`, `models/ttskit/`). Set `BEACON_STT_MODEL=tiny.en`
for fast debugging and `BEACON_TTS=system` if TTSKit is slow or memory is tight.

## Known issues and blockers

- **Speech on the M1:** `tiny.en` processed the test clip in 177–385 ms during a concurrent release build. System speech
  reported start in 264 ms and interruption in 1 ms. TTSKit missed both targets under that load. Use `BEACON_TTS=system`
  for the demo. See [bench.md](bench.md) for test conditions and the remaining live checks.
- **Demo data:** fake Mail mailbox (On My Mac > Import > Beacon Demo, from `demo/seed/make_demo_mbox.py`) and a fake
  "Beacon Demo" Notes folder (iCloud). Record fixtures only from those, never from Justin's real mail/notes.

- **Bonsai latency (biggest risk):** 4B prompt processing is ~150-170 tok/s on this M1. 316-token prompt: 4.3 s cold,
  2.2 s with prefix cache. 4,899-token screen: 32.7 s. `tool_choice: required`, `enable_thinking: false` and
  `cache_prompt` are all accepted and enforced. Needs the 2B and/or much smaller snapshots; Emmet decides via bench.py.
- `start_bonsai.sh` skips the vision projector (memory); `BEACON_VISION=1` re-enables it.
- macOS ties Accessibility, Microphone and Screen Recording permissions to the signed app. Launch the current signed
  app through LaunchServices and verify `/health` before testing. Direct `dump` can also depend on the launching shell's
  Accessibility permission. Screen Recording is only needed for screenshots and P2.
- `brain/`, `mock_helper/` and `scripts/bench.py` were absent from the branches available at handoff. Emmet owns them.

## Next steps

1. Enable Accessibility for Beacon Helper and verify it in `GET /health`. The copy installed before this session predates
   the Mail WebKit walker fix. Capture with a signed build containing `8bdc880` or later.
2. Select Sarah Chen's "Lunch tomorrow?" in the local Beacon Demo mailbox. Record `mail_inbox` and verify that the
   message body contains `noodle place on College Ave` and `build_ms` is at most 300. The original untracked inbox
   snapshot predates the walker fix, shows a different selected message, and must be re-recorded.
3. Capture `mail_reply_draft` before entering the test text, and `notes_list` from the five-note Beacon Demo folder.
   Review the captures for personal data, validate all fixtures with `scripts/contract_test.sh --fixtures-only`, and
   commit them. Emmet needs `mail_inbox` and `safari_form` first.
4. Run `scripts/contract_test.sh` and `scripts/mail_reply_test.sh` against the current signed Helper.
5. Run the live push-to-talk, HUD and barge-in checks in `helper/TESTING.md`. The debug build and regular unit tests are
   already verified (54 pass across two runs, four opt-in benchmarks skipped in the unit suite).
6. Have Emmet review the v1 contracts and bring the Brain to the first offline integration. Start S2 after X2.
