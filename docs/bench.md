# Beacon performance measurements

## Mac Helper, 2026-11-14

Measurements used the 8 GB M1 Mac on macOS 27.0.1, with local models and the debug XCTest build from `a1e1cd9`. The timing runs overlapped a Swift release build and other work on the Mac. Model files were already present.

| Stage | Spec target | Measured |
| --- | ---: | --- |
| WhisperKit tiny.en transcription | 500 ms | 385, 207, 177 ms for three passes of the same 3,046 ms clip |
| System voice enqueue to `tts.started` | 500 ms | 264 ms |
| System voice stop to interrupted `tts.finished` | 100 ms | 1 ms |
| TTSKit enqueue to `tts.started` | 500 ms | 5,708 and 6,189 ms for two utterances |
| TTSKit stop to interrupted `tts.finished` | 100 ms | 163 ms, exceeding the benchmark limit |

The tiny.en run used a synthesized clip and produced the exact transcript. Loading and prewarming took 43.83 s and increased the test process's physical footprint by approximately 82 MiB. Transcription timing covers the PCM buffer through the final text.

TTSKit took 201.46 s to prepare, including 116.23 s to load the model and cache the voice prompt. Its first generated audio during warm-up arrived after 1,505 ms and its physical footprint increased by approximately 265 MiB. The Helper selects system speech when TTSKit warm-up exceeds 1 s. Use `BEACON_TTS=system` to start the demo with system speech and skip TTSKit preparation.

Speech output timing uses local queue events as proxies for audible output. A live push-to-talk run still needs to measure key release through `stt.final`, first audible speech and key press to silence.

Run the same local checks from `helper/` in the Mac desktop session. The TTS tests play audio.

```bash
BEACON_SPEECH_BENCH=1 BEACON_BENCH_MODELS=tiny.en swift test --filter SpeechBenchTests
```

## Bonsai tool-call latency (Brain, Windows owner)

Rule (spec 6.1): use the largest model that turns a snapshot into a tool call in under 2.5 s on the M1.

Reproduce with `scripts/bench.py --model <2B|4B> --sizes 20,40,80` while Bonsai is serving on :8080.
The script prewarms the system prompt + tools (as the Brain does), then sweeps snapshot sizes.

### Decision

**Bonsai 2 2B**, with a warm prompt prefix and capped snapshots (`docs/decisions.md`).
The 4B can't meet the budget on the M1 because prompt processing is too slow.

### M1 Mac, 8 GB (demo machine), measured by Justin

| Model | Prompt processing | 3,000-token screen | 4,900-token screen | Cached prefix, new request |
| --- | ---: | ---: | ---: | ---: |
| 4B | ~170 tok/s | ~17 s | 33 s | ~2.2 s |

The PrismML server accepts `tool_choice: "required"`, `chat_template_kwargs.enable_thinking=false` and `cache_prompt`.
With Bonsai 2 2B and the Brain's v3 prompt, warm prompt processing reached about 394–405 tokens/s and generation
reached 40–42 tokens/s. On the recorded Mail inbox, the 20-, 40-, and 41-element screen summary requests took
6.48 s, 4.81 s, and 7.60 s. The button-location and heading requests returned tool calls in 2.15 s and 1.89 s on
the full fixture. On the live Safari form, the model picked the Saturday control in 2.03 s and the Helper pressed
it in 42 ms. These model timings exclude microphone capture and speech playback.

```bash
brain/.venv/bin/python scripts/bench.py --model 2B --prompt brain/prompts/system_v3.txt --sizes 20,40
```

### Windows laptop (development only): i7-1260P, 16 GB, CPU build, 12 threads

Bonsai 2 2B (`Ternary-Bonsai-2-2B-PQ2_0.gguf`), `-c 16384 -np 1`, fixture `contracts/fixtures/safari_form.json`
(40 elements, 515 snapshot tokens).

- The system prompt + 13 tool schemas take **1,688 tokens**: 21.9 s cold, 0.08 s once cached.
- After that, each request processes only the snapshot plus utterance (~520 tokens), at **66–95 tok/s** here.
- Generation runs at **~29 tok/s**. A tool call is 25–35 tokens (~1 s). One `say` answer ran to 138–263 tokens because the
  model wrote its answer as plain text *outside* the tool call and then called `ask_user`. Prompt v2 targets that.

| Snapshot | Utterance | Tool | New prompt tokens | Time |
| ---: | --- | --- | ---: | ---: |
| 20 el. | What is on my screen? | `ask_user` | 783 | 12.5 s |
| 40 el. | What is on my screen? | `say` | 539 | 15.5 s (219 generated) |
| 40 el. | Click Submit. | `click` id 21 ✔ | 513 | 8.7 s (26 generated) |
| 40 el. | Read the first heading aloud. | `read_element` | 516 | 8.7 s |

This laptop is roughly 3–5× slower than an M1 at prompt processing, so use it to check behaviour, not latency.

### What this means for the budget (estimate, to confirm on the M1)

Time ≈ new prompt tokens ÷ prompt speed + generated tokens ÷ generation speed. With the prefix warm, a 40-element
screen is ~520 new tokens. To fit 2.5 s, the 2B on the M1 needs about 350+ tok/s of prompt processing, plus
generation that stays at tool-call length (~30 tokens). Snapshots over ~80 elements will probably miss the budget, so
the Brain asks for `max_elements` 120 by default and `BEACON_SNAPSHOT_MAX_ELEMENTS` can lower it.

### Evaluation (B5), Bonsai 2 2B on Windows, synthetic screens (`--fixtures mock_helper/fixtures`)

| Prompt | Temperature | Score (target 27/30) | Injection | Text outside tool call |
| --- | ---: | ---: | --- | ---: |
| v1 (spec) | 0.7 | 16/30 | pass | not measured |
| v2 | 0.7 | 21/30 (20 under the old scorer) | pass | 5/31 |
| v3 (worked examples, none taken from the eval screens) | 0.7 | 21/30 | pass | 5/31 |
| v3 | 0.2 | 22/30 | pass | 5/31 |
| **v3 + grounding check (default)** | **0.2** | **26/30 typical** (four runs: 28, 26, 26, 26) | pass | 1–5/31 |

The grounding check (`brain/beacon_brain/grounding.py`) turned all four ambiguous cases into the right question on
every run and changed no other case. The score is **usually 26, one below the 27 target**. The same four model
mistakes remain:
- the "Send me event updates" checkbox gets `confirm` instead of `set_value` (harmless live: the user says yes)
- "where is Submit" gives no position
- the unlabeled button gets asked about instead of located
- the Notes row is read instead of the note's body

Two of these passed once in four runs.

All 6 screen questions, 12 of 12 single actions bar the checkbox, and all 4 risk cases now pass. What still fails:

- **Ambiguous requests:** without grounding, 0/4 ("click the button", "type my message", "open one of my notes",
  "click Continue" when it's absent). The 2B acts instead of asking, even with worked examples. Fixed in code by the
  grounding check: 4/4 on both runs.
- `form_check_updates`: the model asks to confirm because "Send" is in "Send me event updates". The code risk
  guard no longer does this (checkboxes are excluded).
- `form_locate_submit`: the answer gives no position. The snapshot text has no coordinates, only reading order.
- `notes_read_checklist`: the model reads the note's row, not its body. That's arguably acceptable.
- `form_unlabeled_button`: the model asks instead of locating the button by its neighbour.

### Live loop check (Windows, real 2B + Mock Helper + Brain)

- "What's on my screen?": `say` in 4.4 s.
- "Click reply": `click`, then `done` in 7.3 s. Before the ALREADY DONE hint, the model kept clicking and tried to press **Send**;
  the risk guard stopped it.
- "Type: I'll be ten minutes late": `type_text` into the body, then `done`. The model's summary claimed "Sent a message";
  the honesty guard now replaces that with "I typed: …. Nothing has been sent yet."

- "click the button": Beacon asks "Which button: New Message, Reply or the unlabeled button near Reply?" (3.1 s);
  "the reply one": `click` Reply, then `done`. The first live try asked twice, because the answer replaced the
  original request. Memory now keeps `REQUEST` plus `USER ANSWERED "<question>": <reply>`.

### Checks from spec 15.2

- [x] `tool_choice: "required"` accepted (M1 and Windows)
- [x] `cache_prompt` accepted; prefix reuse confirmed via `timings.cache_n` (1,705 of 2,221 tokens cached)
