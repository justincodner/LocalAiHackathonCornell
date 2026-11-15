"""Async client for the local Bonsai llama.cpp server."""

import asyncio
import json
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


class BonsaiError(RuntimeError):
    """The model server did not return a usable completion."""


class _InvalidToolCall(BonsaiError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    stray_text: str = ""  # text the model wrote outside the tool call; costs generation time


@dataclass(frozen=True)
class Completion:
    tool_call: ToolCall
    prompt_tokens: int | None
    generation_tokens_per_second: float | None
    timings: dict[str, Any] | None = None  # llama-server: prompt_n (uncached), prompt_per_second, cache_n


class BonsaiClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float = 12.0,
        *,
        temperature: float = 0.7,
        top_p: float = 0.8,
        top_k: int = 20,
        presence_penalty: float = 1.5,
        max_tokens: int = 300,
        cache_prompt: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        attempts: int = 2,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers={"Authorization": "Bearer beacon"},
            timeout=timeout_s,
            transport=transport,
            trust_env=False,
        )
        self._timeout_s = timeout_s
        self._attempts = max(1, attempts)  # 1 for the optional question model: fail fast, then fall back
        self._sampling: dict[str, Any] = {
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "presence_penalty": presence_penalty,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": cache_prompt,
            # With no tool call, the 2B can loop "answer / say "answer" / </think>" until
            # max_tokens (about 6 s). Nothing valid ever contains </think> with thinking off.
            "stop": ["</think>"],
        }
        self._model: str | None = None
        self._required_tool_choice = True
        # Server slots, read from /props. With more than one, every prewarmed prompt family keeps
        # a slot of its own (see _slot_for); other families share whatever slots are left.
        self._root_url = re.sub(r"/v1/?$", "", base_url.rstrip("/")) + "/"
        self._slot_count = 1
        self._slots: OrderedDict[tuple[str, tuple[str, ...]], int] = OrderedDict()
        self._warm_families: set[tuple[str, tuple[str, ...]]] = set()

    async def __aenter__(self) -> "BonsaiClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def discover_model(self) -> str:
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._http.get("models")
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise BonsaiError("Could not read Bonsai's model list") from exc

        models = body.get("data") if isinstance(body, dict) else None
        if isinstance(models, list):
            for model in models:
                model_id = model.get("id") if isinstance(model, dict) else None
                if isinstance(model_id, str) and model_id.strip():
                    self._model = model_id
                    await self._discover_slots()
                    return model_id
        raise BonsaiError("Bonsai reported no loaded model")

    async def _discover_slots(self) -> None:
        """Read the server's slot count; without it, requests stay unpinned."""
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._http.get(f"{self._root_url}props")
                response.raise_for_status()
                total = response.json().get("total_slots")
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            return
        if type(total) is int and total > 0:
            self._slot_count = total

    def _slot_for(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]],
                  *, warm: bool = False) -> int | None:
        """The slot holding this prompt family's cached prefix, or None to let the server choose.

        Bonsai 2 is a hybrid model: a cached prefix is reusable only up to a saved checkpoint, and
        llama-server's RAM prompt cache keeps just 4-5 prompts. Families sharing one slot evicted
        each other, and an evicted family re-read its whole ~1,400-2,500-token prefix (10-19 s on
        the Windows laptop). A family is its system prompt plus tool names. Prewarmed families
        keep their slot for good; the others take turns on the rest, least recently used first.
        """
        if self._slot_count <= 1:
            return None
        system = messages[0].get("content") if messages and messages[0].get("role") == "system" else ""
        names = tuple(str(tool.get("function", {}).get("name", "")) for tool in tools)
        family = (system if isinstance(system, str) else json.dumps(system), names)
        if family in self._slots:
            self._slots.move_to_end(family)
        elif len(self._slots) < self._slot_count:
            self._slots[family] = len(self._slots)
        else:
            oldest = next((key for key in self._slots if key not in self._warm_families), None)
            if oldest is None:
                return None
            self._slots[family] = self._slots.pop(oldest)
        if warm:
            self._warm_families.add(family)
        return self._slots[family]

    async def prewarm(self, system_prompt: str, tools: Sequence[Mapping[str, Any]]) -> float:
        """Fill the server's prompt cache with the fixed system prompt + tool schemas (spec 10.3).

        Every real request starts with the same prefix, so afterwards the server only
        processes the new snapshot. Returns the seconds the warm-up took.
        """
        model = self._model or await self.discover_model()
        payload = {
            **self._sampling,
            "model": model,
            "max_tokens": 1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "SCREEN:\n\nREQUEST: hello"},
            ],
            "tools": [dict(tool) for tool in tools],
        }
        slot = self._slot_for(payload["messages"], tools, warm=True)
        if slot is not None:
            payload["id_slot"] = slot
        loop = asyncio.get_running_loop()
        started = loop.time()
        limit = max(self._timeout_s, 120)  # a cold prefix takes ~20 s on a laptop CPU
        try:
            async with asyncio.timeout(limit):
                response = await self._http.post("chat/completions", json=payload, timeout=limit)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise BonsaiError("Could not prewarm Bonsai's prompt cache") from exc
        if response.status_code >= 400:
            raise BonsaiError(f"Bonsai prewarm failed (HTTP {response.status_code})")
        return loop.time() - started

    async def next_tool_call(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolCall:
        return (await self.complete(messages, tools)).tool_call

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Completion:
        model = self._model or await self.discover_model()
        can_say = any(tool.get("function", {}).get("name") == "say" for tool in tools)
        payload = {
            **self._sampling,
            "model": model,
            "messages": [dict(message) for message in messages],
            "tools": [dict(tool) for tool in tools],
        }
        slot = self._slot_for(payload["messages"], tools)
        if slot is not None:
            payload["id_slot"] = slot
        for attempt in range(self._attempts):
            try:
                response, allow_text = await self._request_completion(payload)
                # A plain-text answer when say is on offer is that answer, not a failure.
                allow_text = allow_text or can_say
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if attempt < self._attempts - 1 and (status in {408, 429} or status >= 500):
                    continue
                raise BonsaiError(f"Bonsai completion failed (HTTP {status})") from exc
            except (httpx.TransportError, TimeoutError) as exc:
                if attempt < self._attempts - 1:
                    continue
                if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                    raise BonsaiError("Bonsai completion timed out") from exc
                raise BonsaiError("Could not connect to Bonsai for completion") from exc

            try:
                body = response.json()
                call = _parse_tool_call(body, allow_text=allow_text)
            except (ValueError, _InvalidToolCall) as exc:
                if attempt == self._attempts - 1:
                    raise BonsaiError("Bonsai did not return one valid tool call") from exc
                payload["messages"].append(
                    {"role": "user", "content": "Reply with exactly one tool call"}
                )
                payload["presence_penalty"] = min(payload["presence_penalty"], 1.0)
                continue

            usage = body.get("usage")
            timings = body.get("timings")
            prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            speed = timings.get("predicted_per_second") if isinstance(timings, dict) else None
            return Completion(
                call,
                prompt_tokens if type(prompt_tokens) is int and prompt_tokens >= 0 else None,
                float(speed) if type(speed) in (int, float) and speed >= 0 else None,
                timings if isinstance(timings, dict) else None,
            )

        raise AssertionError("Completion retry must return or raise")

    async def _request_completion(self, payload: dict[str, Any]) -> tuple[httpx.Response, bool]:
        async with asyncio.timeout(self._timeout_s):
            if self._required_tool_choice:
                response = await self._http.post(
                    "chat/completions", json={**payload, "tool_choice": "required"}
                )
                if not _rejects_required_tool_choice(response):
                    return response, False
                self._required_tool_choice = False
            return await self._http.post("chat/completions", json=payload), True


def _rejects_required_tool_choice(response: httpx.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    explanation = response.text.casefold().replace("tool choice", "tool_choice")
    return "tool_choice" in explanation and "required" in explanation


def _parse_tool_call(body: Any, *, allow_text: bool) -> ToolCall:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _InvalidToolCall("Expected one completion choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise _InvalidToolCall("Completion had no message")
    calls = message.get("tool_calls")
    if allow_text and (calls is None or calls == []):
        text = _spoken_text(message.get("content"))
        if text:
            return ToolCall(f"call_{uuid4().hex}", "say", {"text": text})
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        raise _InvalidToolCall("Expected exactly one tool call")

    call = calls[0]
    function = call.get("function")
    if not isinstance(function, dict):
        raise _InvalidToolCall("Tool call had no function")
    call_id, name, raw_arguments = call.get("id"), function.get("name"), function.get("arguments")
    if not isinstance(call_id, str) or not call_id.strip() or not isinstance(name, str) or not name.strip():
        raise _InvalidToolCall("Tool call had no ID or function name")
    if not isinstance(raw_arguments, str):
        raise _InvalidToolCall("Tool arguments were not encoded as JSON")
    arguments = json.loads(raw_arguments, parse_constant=_reject_json_constant)
    if not isinstance(arguments, dict):
        raise _InvalidToolCall("Tool arguments were not an object")
    content = message.get("content")
    stray = content.strip() if isinstance(content, str) else ""
    return ToolCall(call_id, name, arguments, stray)


def _spoken_text(content: Any) -> str:
    """The answer in a plain-text reply: its first paragraph, without a written-out say call."""
    if not isinstance(content, str):
        return ""
    first = content.split("</think>", 1)[0].strip().split("\n\n", 1)[0].strip()
    match = re.fullmatch(r'say\s*[:(]?\s*"(.+)"\s*\)?', first, re.DOTALL)
    return (match.group(1) if match else first).strip()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")
