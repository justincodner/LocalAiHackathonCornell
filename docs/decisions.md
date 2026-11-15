# Decisions

Append only, one line per entry: `YYYY-MM-DD: decision (reason).` (spec 12.2)

2026-11-14: Helper HTTP server uses FlyingFox (no extra deps, smaller build than Hummingbird).
2026-11-14: Default BEACON_STT_MODEL is tiny.en: large-v3-626MB measured 1.2-1.3 s per 3 s clip and 130-170 s load on the M1 8 GB (tiny.en: ~80 ms, 1.3 s load).
2026-11-14: Brain: Default model is Bonsai 2 **2B**, not 4B: Justin measured the 4B prompt speed on the M1 at ~170 tok/s (3,000-token screen ≈ 17 s), against a 2.5 s budget. Details in `docs/bench.md`.
2026-11-14: Brain: The Brain prewarms the system prompt and tool schemas into llama-server's prompt cache at start-up (~1,690 tokens, never re-processed after that). It asks for `max_elements` 120 (`BEACON_SNAPSHOT_MAX_ELEMENTS`) instead of 250. Each turn then pays only for the new snapshot and the generated tokens.
2026-11-14: Brain: Default prompt is `brain/prompts/system_v3.txt` at temperature 0.2. Evals on the 2B: v1 16/30, v2 21/30 (rescored), v3 21/30 at 0.7, 22/30 at 0.2. v3's examples deliberately avoid the eval screens.
2026-11-14: Brain: Code guards on top of the model: ids are stripped from spoken text; checkbox `"true"` becomes `true`; after an action the model is told what's ALREADY DONE (stops repeat clicks); and a `done` summary claiming "sent/deleted/..." with no confirmed action is replaced with what actually happened.
2026-11-14: Brain: Added a code-level grounding check (`grounding.py`). Generic or absent click targets and invented typed text become `ask_user`; an obvious mis-pick is retargeted. Evals: typically 26/30 (one run 28; target 27), up from 22. Reviewed twice by a subagent; a model `confirm` now covers an action only if it names the control's full label (`risk.covered_by`).
2026-11-14: Brain: llama-server must run with one slot (`-np 1`), so the warm prefix isn't evicted by a second slot. `scripts/start_bonsai.ps1` already passes it; `run_all.sh` should too.
2026-11-14: Brain: Optional two-model routing (route.py): questions go to the 4B (BEACON_LLM_QUESTION_URL, e.g. :8082), actions to the 2B; off unless that variable is set, and a failing 4B falls back to the 2B (4B evals 29/30 vs 2B 26/30, but ~2x slower per step).
2026-11-14: Brain: Conversation memory across requests: a 240-token rolling memory (`memory.Conversation`) used only for follow-ups. A follow-up question is rewritten once into a standalone request, and the rewrite is accepted only if every added word appears in the conversation. Otherwise, and for actions, the history goes with the request. Live 2B: 8/8 follow-ups answered in context; standalone requests cost nothing extra.
