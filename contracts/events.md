# Helper WebSocket events (contract v1)

Endpoint: `ws://127.0.0.1:8765/events`. The Helper pushes; the Brain only listens (anything it sends is ignored).
Every message is one JSON text frame: `{"type": "...", "ts": <unix ms>, ...extra fields}`.

| Type | Extra fields | When |
| --- | --- | --- |
| `listen.started` | none | Talk key pressed (or `POST /stt/listen {"mode":"start"}`), microphone open |
| `stt.partial` | `text` | During speech, at most every 300 ms (optional; may never be sent) |
| `stt.final` | `utterance_id` (`u_0001`), `text`, `audio_ms`, `stt_ms`; optional `input`, `focus` (see below) | Final transcript ready. Also sent by `POST /debug/utterance` with `audio_ms` and `stt_ms` = 0, and for a typed request (`input: "typed"`, ids `u_typed_0001`, `audio_ms` and `stt_ms` = 0) |
| `listen.cancelled` | `reason` (`too_short`, `empty`, `cancelled`, `stt_model_missing`, `shortcut`, ...) | Utterance under 300 ms, empty transcript, or cancelled |
| `barge_in` | none | Talk key pressed, or a typed request submitted, while speech was playing. The Helper has already stopped audio; the Brain should cancel the task |
| `conversation.reset` | `reason` | The user pressed the X on a screenshot in the HUD. The Helper has dropped the screenshot; the Brain should cancel any task and forget the conversation |
| `model.question` | `enabled` (bool) | The user toggled "Use 4B model for questions" in the HUD's ⋯ menu. The Brain starts or stops the Bonsai 4B on 127.0.0.1:8081 and reports `question_model` (`"loading"`, `true`, `false`) on `/hud` |
| `tts.started` | `speech_id` (`sp_0001`) | First audio sample played |
| `tts.finished` | `speech_id`, `interrupted` (bool) | Speech ended or was stopped |
| `app.changed` | `app`, `window` | Frontmost app changed (the Helper's own process is never reported) |

Ordering guarantees: a talk-key `barge_in` is followed by `listen.started`; a typed-request `barge_in` is followed by that request's `stt.final`. For one spoken utterance, `listen.started` precedes exactly one of `stt.final` or `listen.cancelled`. Typed requests have no `listen.started`.

## Typed requests and selection focus

The user can type a request instead of speaking (Control+Option+Space or the HUD keyboard button). It arrives as an ordinary `stt.final` with `"input": "typed"`; the Brain treats it exactly like speech (including as the answer to a question Beacon is asking).

The user can also select part of the screen (Control+Option+S or the HUD viewfinder button) and then ask about it by voice or by typing. The Helper reads the region on-device (Vision OCR plus labels and values of accessibility elements inside it; no image is sent) and attaches it to the **next** `stt.final` only:

| `focus` field | Type | Meaning |
| --- | --- | --- |
| `text` | string, at most 4000 characters | What the selection says: OCR lines in reading order, then `Controls in the selection:` lines (`- button Pay now`) the OCR missed |
| `source` | `"selection"` | Where the focus came from |
| `app` | string, optional | Frontmost app when the region was selected |
| `bounds` | `[x, y, w, h]` | Selected rectangle, global points, top-left origin (as element `bounds`) |

The Brain answers an utterance with `focus` as a question about that text (the selected text is the primary context, bounded to 4000 characters), takes no window snapshot for it, and starts a new task even if it was waiting for an answer. A selection with no question within about 30 s is dropped and never sent. A HUD placement command or a lone "Hey Beacon" does not consume it.

Example:

```json
{"type": "stt.final", "ts": 1731605000123, "utterance_id": "u_0007", "text": "what's on my screen", "audio_ms": 1840, "stt_ms": 372}
{"type": "stt.final", "ts": 1731605009000, "utterance_id": "u_0008", "text": "what do I owe?", "audio_ms": 1200, "stt_ms": 310,
 "focus": {"text": "Invoice 42\nDue December 1\nTotal  $118.00", "source": "selection", "app": "Safari", "bounds": [420, 310, 360, 140]}}
```
