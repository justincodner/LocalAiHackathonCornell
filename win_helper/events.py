"""WebSocket event hub (contracts/events.md). Safe to emit from any thread."""

import asyncio
import time
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect


class Events:
    def __init__(self) -> None:
        self.listeners: set[asyncio.Queue[dict]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.observers: list = []  # callables(event) run on the emitting thread, e.g. the HUD log

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, kind: str, **fields: object) -> None:
        event = {"type": kind, "ts": time.time_ns() // 1_000_000, **fields}
        for observer in self.observers:
            with suppress(Exception):
                observer(event)
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._deliver(event)
        else:
            self.loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict) -> None:
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
