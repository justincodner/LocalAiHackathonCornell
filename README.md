# LocalAiHackathonCornell
Our project for the Local AI Hackathon at cornell

## Mac quick start

Run once while online (installs git-lfs, uv and jq, clones `../Bonsai-demo` and downloads Bonsai 2 4B):

```bash
scripts/setup_mac.sh                 # BONSAI_ALSO_2B=1 to also fetch the 2B
scripts/build_helper.sh --install    # signed "Beacon Helper.app" in build/ and /Applications
```

Then, online or offline:

```bash
scripts/run_all.sh                   # Bonsai (127.0.0.1:8080) + Helper (127.0.0.1:8765) + Brain; logs in logs/
scripts/contract_test.sh             # check every Helper endpoint against contracts/ (--live also clicks and types)
scripts/dump_fixture.sh mail_inbox   # save the frontmost window as contracts/fixtures/mail_inbox.json
```

Grant Microphone and Accessibility to Beacon Helper on first launch. Demo pages and the rehearsal checklist are in `demo/`; manual Helper checks are in `helper/TESTING.md`.
