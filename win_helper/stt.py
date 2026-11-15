"""Microphone capture and offline transcription (faster-whisper, tiny.en by default)."""

import itertools
import os
import threading
import time
from pathlib import Path

import numpy as np

from .events import Events

RATE = 16000
MIN_MS = 300
SILENCE_RMS = 0.012


def models_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    return Path(os.environ.get("BEACON_MODELS_DIR", root / "models"))


class Listener:
    """One utterance at a time. `start()`/`stop()` come from the talk key; `start(auto_stop=True)` from
    /stt/listen, which ends on 1.2 s of silence after speech (or 4 s of nothing)."""

    def __init__(self, events: Events, on_state=None) -> None:
        self.events = events
        self.on_state = on_state or (lambda state, text=None: None)
        self.model_name = os.environ.get("BEACON_STT_MODEL", "tiny.en")
        if self.model_name.startswith(("large", "openai_whisper")):
            self.model_name = "tiny.en"  # WhisperKit names on the Mac; faster-whisper uses its own
        self.model = None
        self.model_error = ""
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.stream = None
        self.chunks: list[np.ndarray] = []
        self.started = 0.0
        self.auto_stop = False
        self.heard_speech_at = 0.0
        threading.Thread(target=self.load, name="stt-load", daemon=True).start()

    @property
    def listening(self) -> bool:
        return self.stream is not None

    def load(self) -> None:
        try:
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from faster_whisper import WhisperModel
            options = dict(device="cpu", compute_type="int8", download_root=str(models_dir() / "faster-whisper"))
            try:  # offline first: Beacon must not touch the network once set up
                self.model = WhisperModel(self.model_name, local_files_only=True, **options)
            except Exception:  # noqa: BLE001 - not downloaded yet
                self.model = WhisperModel(self.model_name, **options)
            # Warm up so the first real utterance isn't slow.
            self.model.transcribe(np.zeros(RATE // 2, dtype=np.float32), language="en", beam_size=1)
        except Exception as exc:  # noqa: BLE001 - any failure means no speech input, typed input still works
            self.model_error = str(exc)

    def microphone_ok(self) -> bool:
        try:
            import sounddevice as sd
            return sd.query_devices(kind="input") is not None
        except Exception:  # noqa: BLE001
            return False

    def start(self, auto_stop: bool = False) -> bool:
        import sounddevice as sd
        with self.lock:
            if self.stream is not None:
                return False
            self.chunks, self.started, self.auto_stop, self.heard_speech_at = [], time.monotonic(), auto_stop, 0.0
            try:
                self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=self.audio)
                self.stream.start()
            except Exception as exc:  # noqa: BLE001
                self.stream = None
                self.events.emit("listen.cancelled", reason="microphone_unavailable")
                self.on_state("error", f"Microphone: {exc}")
                return False
        self.events.emit("listen.started")
        self.on_state("listening")
        if auto_stop:
            threading.Thread(target=self.watch_silence, daemon=True).start()
        return True

    def audio(self, data, frames, time_info, status) -> None:  # sounddevice callback thread
        chunk = data[:, 0].copy()
        self.chunks.append(chunk)
        if float(np.sqrt(np.mean(chunk ** 2))) > SILENCE_RMS:
            self.heard_speech_at = time.monotonic()

    def watch_silence(self) -> None:
        while self.stream is not None and self.auto_stop:
            now = time.monotonic()
            if self.heard_speech_at and now - self.heard_speech_at > 1.2:
                break
            if not self.heard_speech_at and now - self.started > 4.0:
                break
            if now - self.started > 12.0:
                break
            time.sleep(0.05)
        if self.auto_stop:
            self.stop()

    def cancel(self) -> None:
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is not None:
            stream.stop()
            stream.close()
            self.events.emit("listen.cancelled", reason="cancelled")
            self.on_state("idle")

    def stop(self) -> None:
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is None:
            return
        stream.stop()
        stream.close()
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)
        audio_ms = int(len(audio) / RATE * 1000)
        if audio_ms < MIN_MS:
            self.events.emit("listen.cancelled", reason="too_short")
            self.on_state("idle")
            return
        threading.Thread(target=self.transcribe, args=(audio, audio_ms), daemon=True).start()

    def transcribe(self, audio: np.ndarray, audio_ms: int) -> None:
        self.on_state("thinking", "Transcribing…")
        for _ in range(100):  # the model may still be loading right after start-up
            if self.model is not None or self.model_error:
                break
            time.sleep(0.1)
        if self.model is None:
            self.events.emit("listen.cancelled", reason="stt_model_missing")
            self.on_state("error", "Speech model missing. Run scripts\\setup_windows_helper.ps1 once online.")
            return
        started = time.monotonic()
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1, without_timestamps=True,
                                            condition_on_previous_text=False, vad_filter=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        stt_ms = int((time.monotonic() - started) * 1000)
        if not text or text.strip(" .") == "" or text.casefold() in {"you", "thank you.", "[blank_audio]"}:
            self.events.emit("listen.cancelled", reason="empty")
            self.on_state("idle")
            return
        self.events.emit("stt.final", utterance_id=f"u_{next(self.ids):04d}", text=text,
                         audio_ms=audio_ms, stt_ms=stt_ms)
