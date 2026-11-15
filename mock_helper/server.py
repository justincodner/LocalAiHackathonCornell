"""FastAPI implementation of the local Beacon Helper replay server."""

import asyncio
import itertools
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt

from .replay import DEFAULT_FIXTURES, HelperError, Replay


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


class ColorFilterArgs(BaseModel):
    enabled: bool
    filter: Literal["grayscale", "protanopia", "deuteranopia", "tritanopia", "tint"] | None = None


class HUDArgs(BaseModel):
    state: Literal["idle", "listening", "thinking", "acting", "speaking", "confirm", "error"]
    transcript: str | None = None
    caption: str | None = None
    latency_ms: int | None = None
    highlight: ElementRef | None = None


class Events:
    def __init__(self) -> None:
        self.listeners: set[asyncio.Queue[dict]] = set()

    def emit(self, kind: str, **fields: object) -> None:
        event = {"type": kind, "ts": time.time_ns() // 1_000_000, **fields}
        for listener in self.listeners:
            listener.put_nowait(event)

    async def connect(self, socket: WebSocket) -> None:
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def send_events() -> None:
            while True:
                await socket.send_json(await queue.get())

        self.listeners.add(queue)
        sender = None
        try:
            await socket.accept()
            sender = asyncio.create_task(send_events())
            while (await socket.receive())["type"] != "websocket.disconnect":
                pass
        except WebSocketDisconnect:
            pass
        finally:
            self.listeners.discard(queue)
            if sender is not None:
                sender.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await sender


class Speech:
    def __init__(self, events: Events) -> None:
        self.events = events
        self.ids = itertools.count(1)
        self.active: str | None = None
        self.worker: asyncio.Task[None] | None = None
        self.queue: deque[str] = deque()

    def speak(self, interrupt: bool) -> str:
        if interrupt:
            self.stop()
        speech_id = f"sp_{next(self.ids):04d}"
        self.queue.append(speech_id)
        if self.worker is None:
            self.worker = asyncio.create_task(self.play())
        return speech_id

    async def play(self) -> None:
        while self.queue:
            await asyncio.sleep(0.01)
            self.active = self.queue.popleft()
            self.events.emit("tts.started", speech_id=self.active)
            await asyncio.sleep(0.04)
            self.events.emit("tts.finished", speech_id=self.active, interrupted=False)
            self.active = None
        self.worker = None

    def stop(self) -> asyncio.Task[None] | None:
        self.queue.clear()
        if self.active is not None:
            self.events.emit("tts.finished", speech_id=self.active, interrupted=True)
            self.active = None
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.cancel()
        return worker


def create_app(fixtures_dir: Path | None = None, initial_fixture: str = "mail_inbox") -> FastAPI:
    """Create an independent Helper replay session."""
    replay = Replay(Path(fixtures_dir) if fixtures_dir is not None else DEFAULT_FIXTURES, initial_fixture)
    events = Events()
    speech = Speech(events)
    utterance_ids = itertools.count(1)
    listening = False

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        stopped = speech.stop()
        if stopped is not None:
            with suppress(asyncio.CancelledError):
                await stopped

    app = FastAPI(title="Beacon Mock Helper", version="1", lifespan=lifespan)

    @app.exception_handler(HelperError)
    async def helper_error(_: Request, exc: HelperError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": exc.error})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": {"code": "bad_request", "message": str(exc)}})

    def changed_window(changed: bool) -> None:
        if changed:
            events.emit("app.changed", app=replay.screen["app"], window=replay.screen["window"])

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "version": "mock-1",
            "contract_version": 1,
            "permissions": {"accessibility": True, "microphone": True, "screen_recording": False},
            "stt_model": "mock",
            "tts_engine": "system",
            "online": False,
            "ptt_key": "right_option",
            "dev_lan": False,
        }

    @app.websocket("/events")
    async def events_socket(socket: WebSocket) -> None:
        await events.connect(socket)

    @app.post("/ax/snapshot")
    async def snapshot(args: SnapshotArgs | None = None) -> dict:
        options = args or SnapshotArgs()
        return replay.snapshot(options.scope, min(options.max_elements, 1000))

    @app.post("/ax/find")
    async def find(args: FindArgs) -> dict:
        return {"ok": True, "matches": replay.find(args.query, args.limit)}

    @app.post("/ax/read")
    async def read(args: ElementRef) -> dict:
        return {"ok": True, "text": replay.read(args.snapshot_id, args.element_id)}

    @app.post("/ax/act")
    async def act(args: ActionArgs) -> dict:
        if args.action == "set_value" and args.value is None:
            raise HelperError("bad_request", "set_value needs 'value'")
        changed_window(replay.act(args.snapshot_id, args.element_id, args.action, args.value))
        return {"ok": True, "performed_via": "ax"}

    @app.post("/input/type")
    async def type_text(args: TypeArgs) -> dict:
        return {"ok": True, "chars": replay.type_text(args.text)}

    @app.post("/input/key")
    async def key(args: KeyArgs) -> dict:
        if not args.keys.strip():
            raise HelperError("bad_request", "keys cannot be empty")
        changed_window(replay.key(args.keys))
        return {"ok": True}

    @app.post("/input/scroll")
    async def scroll(args: ScrollArgs) -> dict:
        if args.snapshot_id is not None and args.element_id is not None:
            replay.element(args.snapshot_id, args.element_id)
        return {"ok": True}

    @app.post("/app/open")
    async def open_app(args: OpenArgs) -> dict:
        name, pid, changed = replay.open_app(args.name)
        changed_window(changed)
        return {"ok": True, "app": name, "pid": pid}

    @app.get("/app/list")
    async def apps() -> dict:
        return {"ok": True, "running": [replay.screen["app"]], "installed": ["Mail", "Notes", "Safari"]}

    @app.post("/tts/speak")
    async def speak(args: SpeakArgs) -> dict:
        return {"ok": True, "speech_id": speech.speak(args.priority == "interrupt")}

    @app.post("/tts/stop")
    async def stop_speaking() -> dict:
        speech.stop()
        return {"ok": True}

    @app.post("/stt/listen")
    async def listen(args: ListenArgs) -> dict:
        nonlocal listening
        if args.mode == "start" and not listening:
            if speech.active is not None:
                speech.stop()
                events.emit("barge_in")
            listening = True
            events.emit("listen.started")
        elif args.mode == "stop" and listening:
            listening = False
            events.emit("listen.cancelled", reason="cancelled")
        return {"ok": True}

    @app.post("/hud")
    async def hud(args: HUDArgs) -> dict:
        if args.highlight is not None:
            replay.element(args.highlight.snapshot_id, args.highlight.element_id)
        return {"ok": True}

    # Color filters (helper/BeaconHelper/Display/ColorFilterService.swift), kept in memory.
    color_filter = {"enabled": False, "filter": "grayscale"}

    @app.get("/display/color_filter")
    async def get_color_filter() -> dict:
        return {"ok": True, **color_filter, "available": True}

    @app.post("/display/color_filter")
    async def set_color_filter(args: ColorFilterArgs) -> dict:
        if args.enabled and args.filter is None:
            raise HelperError("bad_request", "turning a color filter on needs 'filter'")
        previous = dict(color_filter)
        color_filter["enabled"] = args.enabled
        if args.filter is not None:
            color_filter["filter"] = args.filter
        return {"ok": True, **color_filter, "previous": previous, "verified": True}

    @app.post("/screen/capture")
    async def capture() -> dict:
        raise HelperError("permission_denied", "The Mock Helper has no screen recording")

    @app.post("/debug/utterance")
    async def utterance(args: TextArgs) -> dict:
        nonlocal listening
        listening = False
        events.emit(
            "stt.final", utterance_id=f"u_{next(utterance_ids):04d}", text=args.text,
            audio_ms=0, stt_ms=0,
        )
        return {"ok": True}

    return app
