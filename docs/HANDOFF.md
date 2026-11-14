# Mac owner handoff (Beacon Helper)

Read this first if you are an AI agent picking up the Mac owner's work. The full design is in `hackthonspec.md`
(sections 4, 5, 9, 11, 12 cover the Helper). Keep this file current: update the status table and "Next steps"
whenever you finish or start something, and commit it.

Last updated: 2026-11-14 18:10 UTC.

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
| H1 Helper shell, server, `/health`, `/events`, `/debug/utterance` | Done, builds |
| Contracts v1 draft (`contracts/`) | Done; needs Emmet's approval via PR (C0) |
| H6 HUD, ring, sounds | Done, 7 tests pass; not yet seen on a real screen |
| H2/H3 accessibility + input | In progress (agent) |
| H4/H5 speech in/out + model downloads | In progress (agent) |
| Scripts, app packaging, demo pages, Bonsai setup | In progress (agent) |
| Fixtures (`mail_inbox`, `mail_reply_draft`, `notes_list`, `safari_form`, `safari_form_unlabeled`) | Not started; needs permissions granted |
| P2 vision label repair (S2) | Not started |

## How to build, test, run

```bash
cd helper && swift build && swift test          # debug build + unit tests
scripts/build_helper.sh --install                # signed "Beacon Helper.app" (stable identity keeps permissions)
scripts/run_all.sh                               # Bonsai + Helper + Brain (Brain only if brain/ exists)
scripts/contract_test.sh                         # endpoint shape checks; --live also clicks/types
scripts/dump_fixture.sh mail_inbox 3             # record a fixture after a 3 s delay
curl -s localhost:8765/health
curl -s -X POST localhost:8765/debug/utterance -d '{"text":"what is on my screen"}'
```

Models live in git-ignored `models/` (`models/whisperkit-coreml/<model>`, `models/ttskit/`). Set `BEACON_STT_MODEL=tiny.en`
for fast debugging and `BEACON_TTS=system` if TTSKit is slow or memory is tight.

## Known issues and blockers

- Accessibility, Microphone (and Screen Recording for screenshots/P2) must be granted by Justin in System Settings to
  the signed app. Until then `Permissions.requestAtLaunch` brings System Settings to the front at each launch,
  which also makes snapshots target System Settings.
- The swift-built debug binary has a different signature from the .app; grant permissions to the .app and test with it.
- HUD was verified only via rendered images (no Screen Recording permission for the shell).

## Next steps

1. Merge the three in-progress agents' work, `swift build && swift test`, fix integration errors.
2. Build the signed app, have Justin grant permissions, run `scripts/contract_test.sh`.
3. Record the five fixtures (needs Mail with a seeded mailbox, Notes, Safari on `demo/form.html` / `demo/checkout.html`);
   commit to `contracts/fixtures/`. Emmet needs `mail_inbox` and `safari_form` first.
4. Verify the H3 acceptance: a curl script clicks Reply in Mail and types into the draft.
5. Measure H4/H5 latency targets (spec 10.2) and record in `docs/bench.md` or `docs/decisions.md`.
6. Open the PR (contracts change needs Emmet's approval).
