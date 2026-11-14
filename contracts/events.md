# Helper WebSocket events (contract v1)

Endpoint: `ws://127.0.0.1:8765/events`. The Helper pushes; the Brain only listens (anything it sends is ignored).
Every message is one JSON text frame: `{"type": "...", "ts": <unix ms>, ...extra fields}`.

| Type | Extra fields | When |
| --- | --- | --- |
| `listen.started` | none | Talk key pressed (or `POST /stt/listen {"mode":"start"}`), microphone open |
| `stt.partial` | `text` | During speech, at most every 300 ms (optional; may never be sent) |
| `stt.final` | `utterance_id` (`u_0001`), `text`, `audio_ms`, `stt_ms` | Final transcript ready. Also sent by `POST /debug/utterance` with `audio_ms` and `stt_ms` = 0 |
| `listen.cancelled` | `reason` (`too_short`, `empty`, `cancelled`, `stt_model_missing`, ...) | Utterance under 300 ms, empty transcript, or cancelled |
| `barge_in` | none | Talk key pressed while speech was playing. The Helper has already stopped audio; the Brain should cancel the task |
| `tts.started` | `speech_id` (`sp_0001`) | First audio sample played |
| `tts.finished` | `speech_id`, `interrupted` (bool) | Speech ended or was stopped |
| `app.changed` | `app`, `window` | Frontmost app changed (the Helper's own process is never reported) |

Ordering guarantees: `barge_in` is followed by `listen.started`. For one utterance, `listen.started` precedes exactly one of `stt.final` or `listen.cancelled`.

Example:

```json
{"type": "stt.final", "ts": 1731605000123, "utterance_id": "u_0007", "text": "what's on my screen", "audio_ms": 1840, "stt_ms": 372}
```
