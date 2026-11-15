"""Async HTTP and WebSocket client for the Helper's v1 contract."""

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect


class HelperError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class HelperClient:
    def __init__(self, base_url: str, token: str = "", *, transport=None, max_elements: int = 250):
        self.max_elements = max_elements
        self.headers = {"X-Beacon-Token": token} if token else {}
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=self.headers, transport=transport,
            timeout=10, trust_env=False,
        )
        url = urlsplit(base_url.rstrip("/"))
        self.events_url = urlunsplit((
            "wss" if url.scheme == "https" else "ws", url.netloc, url.path + "/events", "", "",
        ))

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(self, path: str, payload: dict | None = None) -> dict[str, Any]:
        response = await self.http.request(
            "GET" if payload is None else "POST", path, json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            raise HelperError("invalid_response", f"Invalid Helper response for {path}")
        if not body["ok"]:
            error = body.get("error")
            if not isinstance(error, dict):
                raise HelperError("invalid_response", f"Invalid Helper error for {path}")
            raise HelperError(error.get("code", "unknown"), error.get("message", path))
        return body

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        async with connect(self.events_url, additional_headers=self.headers, proxy=None) as socket:
            async for frame in socket:
                try:
                    event = json.loads(frame)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict) and isinstance(event.get("type"), str):
                    yield event

    async def health(self) -> dict:
        return await self.request("/health")

    async def snapshot(self) -> dict:
        return await self.request("/ax/snapshot", {"scope": "window", "max_elements": self.max_elements})

    async def frontmost(self) -> dict:
        """The frontmost app with only its focused element: ~10 ms, where a window read takes 100-500."""
        return await self.request("/ax/snapshot", {"scope": "focused", "max_elements": 1})

    async def read(self, snapshot_id: str, element_id: int) -> str:
        result = await self.request("/ax/read", {"snapshot_id": snapshot_id, "element_id": element_id})
        return result["text"]

    async def find(self, query: str) -> list[dict]:
        result = await self.request("/ax/find", {"query": query, "limit": 10})
        return result["matches"]

    async def hud(self, state: str, **fields: Any) -> None:
        await self.request("/hud", {"state": state, **fields})

    async def speak(self, text: str, rate: float) -> str:
        result = await self.request("/tts/speak", {"text": text, "rate": rate, "priority": "interrupt"})
        return result["speech_id"]

    async def stop_speech(self) -> None:
        await self.request("/tts/stop", {})

    async def listen(self, mode: str) -> None:
        await self.request("/stt/listen", {"mode": mode})
