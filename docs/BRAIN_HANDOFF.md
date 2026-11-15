# Windows owner handoff (Beacon Brain)

Read this first if you are an AI agent picking up Emmet's work. The design is in `hackthonspec.md`
(sections 6, 7, 8, 12 and 14 cover the Brain). The Mac side has its own `docs/HANDOFF.md`.
Update the status table and "Next steps" whenever you finish or start something.

Last updated: 2026-11-14.

## Ground rules

- Branch `brain/<topic>` (currently `brain/windows-foundation`); merge to `main` by PR.
- Emmet owns `brain/`, `mock_helper/`, `evals/`, `scripts/*.ps1`, `scripts/bench.py`.
  **Never edit `helper/`, `contracts/fixtures/`, `scripts/*.sh`, `demo/`.** `contracts/` changes need both people.
- Only Emmet runs `uv lock`.

## Status

| Workstream | Status |
| --- | --- |
| B1 Bonsai serving, `llm.py`, `bench.py` | Done on Windows. Bonsai-demo is at `..\Bonsai-demo` with the 2B + 4B GGUFs and the CPU llama.cpp build. Results and the **2B decision** are in `docs/bench.md`. Still to do: run `bench.py --model 2B` on the M1. |
| B2 Mock Helper | Done. Covers the full v1 REST surface plus `/events`; replays fixtures, and swaps fixtures on Reply/Send/Escape/`open_app`. Tested in `brain/tests/test_mock_helper.py`. |
| B3 Agent loop, state machine, memory, logging | Done. The acceptance test passes over real sockets (`brain/tests/test_end_to_end.py`). |
| B4 Validation, risk guard, narration, fast paths | Done, with unit tests for each rule. |
| B5 Prompt + 30-scenario evals | **26/30 typical** (four runs: 28, 26, 26, 26; target 27) with prompt v3, temperature 0.2 and the grounding check, on the 2B. The injection case passes. The last four failures are listed in `docs/bench.md`. The likeliest next gain is a position word per element in the snapshot ("where is…"). | The official run still needs Justin's `mail_inbox`, `mail_reply_draft` and `notes_list` recordings. |
| S1 Two-model routing (P2) | Not started (after X2). |

## Run it on Windows

There's no `uv`, no `git` and no Python on PATH on this laptop. Anaconda 3.14 lives at `%USERPROFILE%\anaconda3`.
A venv is in `brain\.venv` (git-ignored); it's the editable Brain plus pytest.

```powershell
brain\.venv\Scripts\python.exe -m pytest brain/tests -q          # 290 tests, ~5 s, no model, no Mac
brain\.venv\Scripts\python.exe -m pytest brain/tests -m loop     # loop and end-to-end tests only
brain\.venv\Scripts\python.exe -m mock_helper                    # terminal 1: mock on 127.0.0.1:8765
brain\.venv\Scripts\python.exe -m beacon_brain                   # terminal 2: needs Bonsai on :8080
brain\.venv\Scripts\python.exe evals\run_evals.py --fixtures mock_helper\fixtures   # synthetic-screen eval
brain\.venv\Scripts\python.exe scripts\bench.py --model 4B       # writes a markdown table for docs/bench.md
```

Once `uv` is installed, the equivalents are `uv run --project brain ...` as in the README.

## Changes since Codex's first pass

- `fastpath.py`: "stop ..." at the start of any request cancels (spec 9.5). "No, cancel", "yeah" and "okay" are recognised.
- `risk.py`: password/card-label refusal applies only to actions. Reading the "Password expiry" email is allowed.
- `loop.py`: retries `stale_snapshot` once on a fresh snapshot if the app and target are unchanged (spec 8.5).
  A model failure now says "I'm having trouble thinking. Please try again."
- Added tests for the Mock Helper, narration, memory, the eval scorer, loop failure paths and two
  end-to-end runs over real HTTP and WebSocket (R1 and story A4).

## Latency work (after Justin's M1 numbers)

- `config.py`: `BEACON_SNAPSHOT_MAX_ELEMENTS` (default 60) is sent as `max_elements` on every snapshot.
  `BEACON_PREWARM` (default on) caches the system prompt + tools prefix at start-up.
  `BEACON_THINKING_FILLER_MS` (default 1500) sets when "One moment." is spoken if the model is still thinking.
  `BEACON_PROMPT_PATH` selects the system prompt file.
- `llm.py`: `prewarm()`. `ToolCall.stray_text` captures text the model writes outside its tool call; the loop logs it as
  `stray_chars`, and evals count it.
- `scripts/start_bonsai.ps1` defaults to the 2B. It and `setup_windows.ps1` had a PowerShell 5.1 bug (empty
  `$PSScriptRoot` in `param()` defaults), now fixed.
- `scripts/bench.py` reports cached tokens and prompt tok/s, and sweeps snapshot sizes (`--sizes`).

Start Bonsai on Windows without the PrismML setup script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_bonsai.ps1 -Model 2B
```

## Two-model routing (`brain/beacon_brain/route.py`)

The 4B scored 29/30 against the 2B's typical 26, but is roughly 2× slower per step. So the Brain can run both:

- **Questions → 4B.** "What's on my screen?", "Where is…", "Read the message…" take one model call, and answer
  quality is what users and judges notice.
- **Actions → 2B.** Clicks, typing and multi-step jobs take several calls, so speed adds up. The code checks
  (confirmations, refusals, grounding, honest summaries) protect them whichever model runs.

How it works:
- `route.classify()` decides from the user's words alone, with no model call: wh-words, "is/are/does…", and
  read/describe/summarize/list/find mean a question. A chained action ("read it **and reply**…") means an action.
  The default is action.
- The route is fixed per task, and the log records `route` and `model` for every step.
- **Failure handling:** the question model gets one attempt with its own short timeout
  (`BEACON_LLM_QUESTION_TIMEOUT_S`, default 8 s). If it fails, the fast model answers, and the Brain skips the
  question model for the next 60 s, so a busy 4B never costs every later step its timeout.
- **Clauses:** chained requests split by punctuation ("Where is the reply button? Click it") count as actions.
- **Results:** routed evals scored 28/30 (questions 9/10 on the 4B, actions 19/20 on the 2B). See `docs/bench.md`.
- **This laptop's CPU:** the 4B is too slow for real Mail screens here (~17 tok/s). For local testing, raise
  `BEACON_LLM_QUESTION_TIMEOUT_S` and `BEACON_TASK_TIMEOUT_S`, or leave routing off.
- **Built with subagents:** the router (`route.py`, 86 tests) and the launcher were written by subagents in
  parallel, and a third reviewed the change. Its two findings (24 s stalls, chained requests sent to the 4B) are fixed.
- **It's off unless `BEACON_LLM_QUESTION_URL` is set.** Empty means one model for everything, exactly as before, so
  `run_all.sh` is unaffected until it opts in.
- If the question server is set but unreachable at startup, the Brain warns and runs on the fast model alone.

Start the pair on Windows (two consoles), then the Brain:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_bonsai.ps1                       # 2B on :8080 (actions)
powershell -ExecutionPolicy Bypass -File scripts\start_bonsai.ps1 -Model 4B -Port 8082  # 4B on :8082 (questions)
$env:BEACON_LLM_QUESTION_URL = "http://127.0.0.1:8082/v1"; brain\.venv\Scripts\python.exe -m beacon_brain
```

`scripts/start_bonsai.ps1` now starts `llama-server.exe` directly, because PrismML's launcher hard-codes port 8080
and refuses to start when anything answers there. It binds to 127.0.0.1 only, starts eight slots with separate
caches (`-np 8 --no-kv-unified`, one per prompt family plus a spare; see `docs/bench.md`), and refuses a busy port. The evals route the same way when `BEACON_LLM_QUESTION_URL` (or `--question-url`) is set, and print a
score per route.

For Justin (Mac): `run_all.sh` would start a second server, `BONSAI_MODEL=4B` on port 8082, and export
`BEACON_LLM_QUESTION_URL`. Both models plus `tiny.en` and the system voice fit in 8 GB (~3.5 GB, estimate).

## Grounding check (`brain/beacon_brain/grounding.py`)

The 2B almost never asks on an ambiguous request, so the Brain checks this in code. It runs after validation and
before the risk guard. Like the risk guard, it doesn't depend on the model following the prompt.

| Situation | Example | Beacon does |
| --- | --- | --- |
| Generic click target with several candidates | "click the button", "open one of my notes" | Asks "Which button: New Message, Reply or …?" |
| Named target not on screen | "click Continue" on a form with only Submit | Asks "I don't see continue on this screen. What should I click instead?" |
| Wrong pick, exactly one control matches the user's words | model picks New Message for "click reply" | Clicks Reply instead |
| Typed text mostly not from the user (<60% of its words) | "type my message" → model invents a message | Asks "What would you like me to type?" |

It's lenient about letting a call through and strict about changing one:
- A click passes if the chosen label has any word the user said ("open my notes" → Notes).
- Retargeting needs at least half the user's words to match, and prefix matching runs one way only ("payment" never matches "Pay").
- A click is moved onto a Pay/Send/Delete control only if the user said that verb and every word they named is in its label. Otherwise it asks "Did you mean …?".
- Typing: digits match number words, and email addresses and URLs are left out of the comparison. The words around them are still checked.

It deliberately leaves alone:
- requests that aren't "click/press/tap/select/open X" (e.g. "reply to Sarah that …")
- pronouns and ordinals ("click it", "the first one")
- later clicks in a multi-step plan (it's only active until the first action after the user speaks)

Logged as `grounded: asked|retargeted`. The evals score the call after grounding, as the Brain would run it.

## Conversation memory (`memory.Conversation`, `followup.py`)

Before this, each request stood alone. A general question had no memory at all ("Who is the president of France?" then
"How old is he?" failed). An action only saw a two-line summary of the request just before it. A full chat history would
not fit: every step already spends the 2B's context and prompt-processing time on the screen.

Memory costs tokens only when the words need it. It also enters the request in just one place.

1. **A small, fixed budget.** `Conversation` keeps the last 4 exchanges as `User: … / Beacon: …`. The newest reply is
   kept whole (clipped to the room left). Older replies are cut to their first sentence, and the oldest are dropped
   once the block passes 240 tokens. It is forgotten after 5 minutes of silence, or when the user says "forget that" or
   "new topic". Settings: `BEACON_MEMORY_TOKENS` (0 turns it off), `BEACON_MEMORY_TURNS` and `BEACON_MEMORY_IDLE_S`.
2. **Follow-ups only.** `followup.is_follow_up` looks for pronouns (he, it, their, there, one), openers ("what about",
   "and", "tell me more") and bare replies ("Why?"). "this" and "here" mean the screen, so they don't count, and "What
   time is it?" is excluded. A standalone request sends no memory and makes no extra model call.
3. **Follow-up questions are rewritten.** One short call (about 50 uncached tokens) turns "How old is he?" into "How old
   is Emmanuel Macron?". From then on, routing, the question prompts, the logs and every code guard that reads the
   user's words see only the standalone request. The history never reaches the question prompt.
4. **Rewrites are grounded.** `followup.checked` accepts a rewrite only if it keeps all of the user's content words and
   every word it adds already appears in the conversation. An answer instead of a rewrite ("Macron is 48") or an
   invented name is rejected.
5. **Fallback.** If the rewrite is rejected, unchanged or failed, the question goes out with the history attached
   (`EARLIER CONVERSATION: … QUESTION: …`). A follow-up action ("Reply to her") is never rewritten, because rewriting
   could make the send guards type text again. It gets the history in its task prompt, before the steps, so the
   history stays in the cached prefix for every later step.
6. **Follow-ups stay on the screen.** A follow-up question to an exchange that used the screen is answered from the
   screen again ("Who sent the latest email here?" then "What does she want?").

Logs record `heard` (what the user said) whenever `utterance` was rewritten.

Live results, Bonsai 2 2B on the Windows laptop:
- Rewrite probe: 7 of 10 follow-ups were rewritten and accepted, using about 50 new prompt tokens each.
- Through the loop: 8 of 8 follow-ups in four conversations were answered in context, 3 through the rewrite and the
  rest through the history fallback. The 2B still gets some facts wrong (for example Macron's age).
- `scripts/loop_scenarios.py follow-up` covers a pronoun, an ellipsis and a follow-up on the screen. All 3 pass;
  the screen one needs `BEACON_LLM_TIMEOUT_S=60` on this laptop, because cold prompts there take about 20 s.

## Review (subagent, two rounds)

An independent review found 6 bugs that the tests missed, then re-probed the fixes and found 4 more. All are now fixed,
each with a regression test:
- `clean_speech` clipped "is on." / "come on!".
- The honesty guard rewrote screen descriptions like "Sarah sent you…", and missed "Okay, sent!" and "saved and sent".
- Clicks could be retargeted by prefix ("payment" → Pay now).
- "open my notes" was asked about needlessly.
- The typing check was too strict (digits, emails), and one URL exempted a whole sentence.
- The model's own `confirm` caused a double confirmation in demo step 5. Fixing that is a safety edge: a model confirm
  now covers an action only if it names the control's full label (or the chord's verb), not negated. "Send Sarah the
  payment reminder" never covers **Pay now**, and "Don't delete anything" never covers **Delete**. See
  `risk.covered_by` and its tests.

Still open (cosmetic): an id spoken after a preposition is dropped with it, so "is at [3]." becomes "is.". The prompt
already forbids speaking ids.

## Known gaps

- The log line has no `tts_first_audio` timing (the Brain doesn't wait for `tts.started`).
- The Brain doesn't send `latency_ms` to the HUD.
- `uv.lock` isn't committed (no `uv` on this machine).

## Next steps

1. Justin: run `scripts/bench.py --model 2B` on the M1 and add the table to `docs/bench.md`. That sets `BEACON_SNAPSHOT_MAX_ELEMENTS`.
2. Justin's recorded Mail/Notes fixtures are now in `contracts/fixtures/` (this branch is based on his). Run the
   official eval without `--fixtures`. Note that Mail's compose body records as a focused `group "message body"`; the mock
   and the scorer accept typing into it.
3. Suggest to Justin (contract): a coarse position word per element in the snapshot text (e.g. "bottom") would let the
   model answer "where is…" questions. It currently only has reading order.
4. Approve the C0 contracts PR; install Git, commit, push `brain/windows-foundation` and open a PR. Only Emmet runs `uv lock`.
