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
| H1 Helper shell, server, `/health`, `/events`, `/debug/utterance` | Debug and release builds passed. Verified `stt.final` delivery over WebSocket from the signed Helper. |
| Contracts v1 (`contracts/`) | [PR #1](https://github.com/justincodner/LocalAiHackathonCornell/pull/1) merged. The Brain and signed Helper connected using v1 on the Mac. |
| H6 HUD, ring, sounds | Monochrome overlay with drag, position menu, voice placement and expanding reply text. Drag, menu and replies verified on the signed app. Voice placement verified with a simulated transcript. |
| H2/H3 accessibility + input | Live Reply opened the seeded Mail draft, typed test text and verified it via Accessibility. Mail inbox snapshot took 296 ms with the message body present. |
| H4/H5 speech in/out + model downloads | Local speech benchmarks in [bench.md](bench.md). System speech met both timing targets. Live push-to-talk and audible barge-in pending. |
| Scripts, app packaging, demo pages, Bonsai setup | Freshly signed workspace app verified. Live contract suite passed 49 checks and skipped P2 screen capture. |
| Fixtures | All five captured and schema-validated. The inbox fixture omits the Mail account sidebar. |
| Brain integration | [PR #2](https://github.com/justincodner/LocalAiHackathonCornell/pull/2) merged. Bonsai 2 2B, Brain, and signed Helper passed a live Safari screen summary and the Saturday radio-button action using debug transcripts. |
| Question routing | Standalone questions use the local model without taking a snapshot. Personal and screen-specific questions use a fresh accessibility snapshot. Browser answers use the active page's contents. |
| Advice about the active page | Advice or requests to choose between visible options use read-only questions. Beacon gives a recommendation based on available page content or explains what it can't read. Unlabeled graphics are still unavailable. |
| P2 vision label repair (S2) | Not started |

## How to build, test, run

```bash
cd helper && swift build && swift test          # debug build + unit tests
scripts/build_helper.sh --install                # signed "Beacon Helper.app" (stable identity keeps permissions)
BEACON_TTS=system scripts/run_all.sh              # Bonsai + Helper + Brain from the prepared local environment
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

- **Stuck on "Thinking":** the Helper outlives `run_all.sh` if that terminal is closed, so Bonsai and the Brain can be
  gone while push-to-talk still works. The Helper now says the Brain isn't running when no `/events` client is
  connected, and drops out of Thinking after 90 s with no Brain update. Restart with `scripts/run_all.sh`.
- **Bonsai latency:** The Mac launcher prefers the installed 2B model. See [bench.md](bench.md) for timings.
- `start_bonsai.sh` skips the vision projector (memory); `BEACON_VISION=1` re-enables it.
- macOS ties Accessibility, Microphone and Screen Recording permissions to the signed app. Launch the current signed
  app through LaunchServices and verify `/health` before testing. The workspace build reported Accessibility and Microphone
  granted, while direct `dump` from the Codex shell was denied. Use `POST /ax/snapshot` on the running signed app if the
  shell lacks access. The installed copy in `/Applications` predates the Mail walker fix. Screen Recording is only needed
  for screenshots and P2.

## Next steps

1. Run the live push-to-talk and audible barge-in checks in `helper/TESTING.md`, including voice placement with the microphone.
2. Rehearse the full demo offline, including the seeded Mail reply and confirmation. Rehearse X2 before S2.
3. Rehearse a page-specific question followed by a standalone question on the same page. Include a question about a value the page does not show.
4. Ask for a recommendation about visible options, then repeat on a page that does not expose the details needed to compare them.
