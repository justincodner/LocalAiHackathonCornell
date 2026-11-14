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

## Bonsai tool-call latency

Awaiting `scripts/bench.py` results for the Mac and Windows runs required by spec 10.4.
