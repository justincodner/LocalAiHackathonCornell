"""The Windows Helper's v1 HTTP + WebSocket API (contracts/openapi.yaml), backed by the real desktop."""

import asyncio
import itertools
import os
import threading
import time
import winsound
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt

from . import keys, win32
from .apps import Apps
from .events import Events
from .uia import HelperError, Reader, com_thread

VERSION = "win-1"
SOUNDS = Path(__file__).resolve().parent.parent / "helper" / "BeaconHelper" / "HUD" / "Sounds"
IGNORED_CLASSES = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow", "Progman", "WorkerW",
    "ForegroundStaging", "MultitaskingViewFrame", "TaskSwitcherWnd",
}


# --- request bodies (same as the Mock Helper) --------------------------------------------------------------------

class ElementRef(BaseModel):
    snapshot_id: str
    element_id: StrictInt


class SnapshotArgs(BaseModel):
    scope: Literal["window", "focused", "menu"] = "window"
    max_elements: int = Field(default=250, ge=1)


class FindArgs(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1)


class ActionArgs(ElementRef):
    action: Literal["press", "focus", "set_value", "show_menu", "increment", "decrement"]
    value: str | int | float | bool | None = None


class TextArgs(BaseModel):
    text: str


class TypeArgs(TextArgs):
    text: str = Field(min_length=1, max_length=2000)


class KeyArgs(BaseModel):
    keys: str


class ScrollArgs(BaseModel):
    direction: Literal["up", "down", "left", "right"]
    amount: Literal["page"] | StrictInt = "page"
    snapshot_id: str | None = None
    element_id: StrictInt | None = None


class OpenArgs(BaseModel):
    name: str


class SpeakArgs(TextArgs):
    priority: Literal["normal", "interrupt"] = "normal"
    rate: float | None = None


class ListenArgs(BaseModel):
    mode: Literal["start", "stop"]


class HUDArgs(BaseModel):
    state: Literal["idle", "listening", "thinking", "acting", "speaking", "confirm", "error"]
    transcript: str | None = None
    caption: str | None = None
    latency_ms: int | None = None
    highlight: ElementRef | None = None


# --- desktop state -----------------------------------------------------------------------------------------------

class Desktop:
    """Tracks the window Beacon should look at: the frontmost one that isn't Beacon's own or the taskbar."""

    def __init__(self, events: Events) -> None:
        self.events = events
        self.target = 0
        self.last = ("", "")
        threading.Thread(target=self.watch, name="foreground", daemon=True).start()

    def usable(self, hwnd: int) -> bool:
        return (bool(hwnd) and win32.window_pid(hwnd) != win32.OWN_PID
                and win32.window_class(hwnd) not in IGNORED_CLASSES)

    def watch(self) -> None:
        while True:
            fg = win32.foreground()
            if self.usable(fg):
                self.target = fg
                try:
                    app = win32.app_name(fg)
                    window = win32.clean_window_title(win32.window_title(fg), app)
                except OSError:
                    app, window = "", ""
                if (app, window) != self.last and app:
                    self.last = (app, window)
                    self.events.emit("app.changed", app=app, window=window)
            time.sleep(0.25)

    def window(self) -> int:
        fg = win32.foreground()
        if self.usable(fg):
            self.target = fg
            return fg
        if win32.is_window(self.target):
            return self.target
        windows = win32.top_windows()
        return windows[0] if windows else 0


def play(name: str) -> None:
    path = SOUNDS / f"{name}.wav"
    if path.exists():
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def create_app(talk_key: str = "right_alt", with_hud: bool = True, with_voice: bool = True) -> FastAPI:
    events = Events()
    desktop = Desktop(events)
    reader = Reader(desktop.window)
    apps = Apps()
    pool = ThreadPoolExecutor(max_workers=1, initializer=com_thread, thread_name_prefix="uia")
    typed_ids = itertools.count(1)

    hud = None
    speech = None
    listener = None

    def hud_state(state: str, text: str | None = None) -> None:
        if hud is not None:
            hud.state(state, caption=text)

    if with_voice:
        from .speech import Speech
        from .stt import Listener
        speech = Speech(events)
        listener = Listener(events, on_state=hud_state)

    def emit_utterance(text: str) -> None:
        events.emit("stt.final", utterance_id=f"t_{next(typed_ids):04d}", text=text, audio_ms=0, stt_ms=0)

    def typed(text: str) -> None:
        """Typed into the Beacon window: hand focus back to the user's app, then send it like speech."""
        if speech is not None and speech.speaking:
            speech.stop()
            events.emit("barge_in")
        if win32.is_window(desktop.target):
            win32.activate(desktop.target)
        emit_utterance(text)

    def talk_down() -> None:
        if listener is None:
            return
        if speech is not None and speech.speaking:
            speech.stop()
            events.emit("barge_in")
        play("listen_start")
        listener.start()

    def talk_up() -> None:
        if listener is None:
            return
        play("listen_stop")
        listener.stop()

    key_label = {"right_alt": "Right Alt", "right_option": "Right Alt", "right_control": "Right Ctrl",
                 "right_ctrl": "Right Ctrl", "right_command": "Right Ctrl", "right_win": "Right Windows key",
                 "scroll_lock": "Scroll Lock", "pause": "Pause", "f13": "F13"}.get(talk_key, "Right Alt")
    if with_hud:
        from .hud import HUD
        hud = HUD(key_label, typed)

        def log_event(event: dict) -> None:
            if event["type"] == "stt.final":
                hud.log("you", event["text"])
            elif event["type"] == "listen.cancelled" and event.get("reason") not in {"cancelled"}:
                hud.log("note", f"(didn't catch that: {event.get('reason')})")
        events.observers.append(log_event)
    if with_voice:
        from .hotkey import TalkKey
        TalkKey(talk_key, talk_down, talk_up)

    dev_lan = os.environ.get("BEACON_DEV_LAN", "0") == "1"
    token = os.environ.get("BEACON_TOKEN", "")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        events.bind(asyncio.get_running_loop())
        yield
        if speech is not None:
            speech.stop()

    app = FastAPI(title="Beacon Windows Helper", version="1", lifespan=lifespan)

    async def run(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)

    @app.middleware("http")
    async def check_token(request: Request, call_next):
        if dev_lan and request.headers.get("X-Beacon-Token") != token:
            return JSONResponse({"ok": False, "error": {"code": "permission_denied", "message": "bad token"}})
        return await call_next(request)

    @app.exception_handler(HelperError)
    async def helper_error(_: Request, exc: HelperError) -> JSONResponse:
        if hud is not None:
            hud.log("note", f"({exc.error['code']}: {exc.error['message']})")
        return JSONResponse({"ok": False, "error": exc.error})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": {"code": "bad_request", "message": str(exc)}})

    @app.exception_handler(keys.ChordError)
    async def bad_chord(_: Request, exc: keys.ChordError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": {"code": "bad_request", "message": str(exc)}})

    @app.exception_handler(OSError)
    async def os_error(_: Request, exc: OSError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": {"code": "action_failed", "message": str(exc)}})

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True, "version": VERSION, "contract_version": 1,
            "permissions": {"accessibility": True,
                            "microphone": bool(listener and listener.microphone_ok()),
                            "screen_recording": False},
            "stt_model": listener.model_name if listener else "none",
            "tts_engine": "system", "online": win32.online(), "ptt_key": talk_key, "dev_lan": dev_lan,
        }

    @app.websocket("/events")
    async def events_socket(socket: WebSocket) -> None:
        if dev_lan and socket.headers.get("X-Beacon-Token") != token:
            await socket.close(code=4401)
            return
        await events.connect(socket)

    @app.post("/ax/snapshot")
    async def snapshot(args: SnapshotArgs | None = None) -> dict:
        options = args or SnapshotArgs()
        return await run(reader.snapshot, options.scope, min(options.max_elements, 1000))

    @app.post("/ax/find")
    async def find(args: FindArgs) -> dict:
        return {"ok": True, "matches": await run(reader.find, args.query, args.limit)}

    @app.post("/ax/read")
    async def read(args: ElementRef) -> dict:
        return {"ok": True, "text": await run(reader.read, args.snapshot_id, args.element_id)}

    @app.post("/ax/act")
    async def act(args: ActionArgs) -> dict:
        if args.action == "set_value" and args.value is None:
            raise HelperError("bad_request", "set_value needs 'value'")
        via = await run(reader.act, args.snapshot_id, args.element_id, args.action, args.value)
        return {"ok": True, "performed_via": via}

    def front_then(fn, *args):
        hwnd = desktop.window()
        if hwnd and win32.foreground() != hwnd:
            win32.activate(hwnd)
        return fn(*args)

    @app.post("/input/type")
    async def type_text(args: TypeArgs) -> dict:
        def typing(text: str) -> int:
            reader.check_typing_target()
            return win32.type_unicode(text)
        return {"ok": True, "chars": await run(front_then, typing, args.text)}

    @app.post("/input/key")
    async def key(args: KeyArgs) -> dict:
        keys.parse(args.keys)  # reject bad chords before touching the keyboard
        await run(front_then, keys.press, args.keys)
        return {"ok": True}

    @app.post("/input/scroll")
    async def scroll(args: ScrollArgs) -> dict:
        await run(reader.scroll, args.direction, args.amount, args.snapshot_id, args.element_id)
        return {"ok": True}

    @app.post("/app/open")
    async def open_app(args: OpenArgs) -> dict:
        return await run(apps.open, args.name)

    @app.get("/app/list")
    async def app_list() -> dict:
        return await run(apps.listing)

    @app.post("/tts/speak")
    async def speak(args: SpeakArgs) -> dict:
        if hud is not None:
            hud.log("beacon", args.text)
        if speech is None:
            speech_id = f"sp_{int(time.time() * 1000) % 100000:05d}"
            events.emit("tts.started", speech_id=speech_id)
            events.emit("tts.finished", speech_id=speech_id, interrupted=False)
            return {"ok": True, "speech_id": speech_id}
        rate = None if args.rate is None else max(0.5, min(1.5, args.rate))
        return {"ok": True, "speech_id": speech.speak(args.text, args.priority == "interrupt", rate)}

    @app.post("/tts/stop")
    async def stop_speaking() -> dict:
        if speech is not None:
            speech.stop()
        return {"ok": True}

    @app.post("/stt/listen")
    async def listen(args: ListenArgs) -> dict:
        if listener is None:
            raise HelperError("permission_denied", "Voice input is off (--no-voice)")
        if args.mode == "start" and not listener.listening:
            if speech is not None and speech.speaking:
                speech.stop()
                events.emit("barge_in")
            play("listen_start")
            await asyncio.to_thread(listener.start, True)
        elif args.mode == "stop" and listener.listening:
            await asyncio.to_thread(listener.cancel)
        return {"ok": True}

    @app.post("/hud")
    async def hud_route(args: HUDArgs) -> dict:
        rect = None
        if args.highlight is not None:
            rect = reader.bounds(args.highlight.snapshot_id, args.highlight.element_id)
        if hud is not None:
            hud.state(args.state, args.transcript, args.caption)
            if rect is not None:
                hud.highlight(rect)
            if args.state == "error":
                play("error")
        return {"ok": True}

    @app.post("/screen/capture")
    async def capture() -> dict:
        raise HelperError("permission_denied", "Screen capture is not implemented in the Windows Helper")

    @app.post("/debug/utterance")
    async def utterance(args: TextArgs) -> dict:
        emit_utterance(args.text)
        return {"ok": True}

    return app
