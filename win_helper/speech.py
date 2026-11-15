"""Text to speech with the Windows voices (SAPI 5), on its own thread so /tts/stop silences within ~50 ms."""

import itertools
import os
import queue
import threading

from .events import Events

SVSF_ASYNC, SVSF_PURGE = 1, 2


class Speech:
    def __init__(self, events: Events) -> None:
        self.events = events
        self.ids = itertools.count(1)
        self.commands: queue.Queue = queue.Queue()
        self.active: str | None = None
        self.lock = threading.Lock()
        self.voice_name = ""
        self.ready = threading.Event()
        threading.Thread(target=self.run, name="tts", daemon=True).start()
        self.ready.wait(5)

    @property
    def speaking(self) -> bool:
        return self.active is not None

    def speak(self, text: str, interrupt: bool, rate: float | None) -> str:
        speech_id = f"sp_{next(self.ids):04d}"
        if interrupt:
            self.stop()
        self.commands.put(("speak", speech_id, text, rate))
        return speech_id

    def stop(self) -> None:
        # Drop anything queued, then silence the current utterance.
        try:
            while True:
                self.commands.get_nowait()
        except queue.Empty:
            pass
        self.commands.put(("stop", None, None, None))

    def run(self) -> None:
        from .uia import com_thread
        com_thread()
        import comtypes.client
        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        wanted = os.environ.get("BEACON_WIN_VOICE", "").casefold()
        voices = voice.GetVoices()
        for i in range(voices.Count):
            token = voices.Item(i)
            if wanted and wanted in token.GetDescription().casefold():
                voice.Voice = token
        self.voice_name = voice.Voice.GetDescription()
        self.ready.set()
        pending: list = []
        while True:
            if self.active is None and pending:
                speech_id, text, rate = pending.pop(0)
                voice.Rate = 0 if rate is None else max(-10, min(10, round((rate - 1.0) * 10)))
                voice.Speak(text, SVSF_ASYNC)
                self.active = speech_id
                self.events.emit("tts.started", speech_id=speech_id)
            try:
                kind, speech_id, text, rate = self.commands.get(timeout=0.03 if self.active else 0.5)
            except queue.Empty:
                kind = None
            if kind == "speak":
                pending.append((speech_id, text, rate))
            elif kind == "stop":
                pending.clear()
                if self.active is not None:
                    voice.Speak("", SVSF_ASYNC | SVSF_PURGE)
                    self.events.emit("tts.finished", speech_id=self.active, interrupted=True)
                    self.active = None
            if self.active is not None and voice.WaitUntilDone(0):
                self.events.emit("tts.finished", speech_id=self.active, interrupted=False)
                self.active = None
