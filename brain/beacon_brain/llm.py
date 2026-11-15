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


# Generation runs ~25-30 tok/s, so every unused token of max_tokens a runaway reply can fill costs
# ~35 ms. Caps per offered tool list, from logs/session.jsonl, logs/bonsai.log, docs/bench.md and
# evals/ (~4 chars/token, ~15 tokens of tool-call JSON):
# - id/key/name-only tools (click, set_value, scroll, ...): calls measured 10-35 tokens -> 64.
# - short free text (ask_user, done, confirm, find, rewrite): longest seen ~100 chars, ~40 tokens;
#   a rewritten request is about the length of the utterance -> 128.
# - say: prompts ask for <= 40 words; the longest valid say was 270 chars (~85 tokens with
#   the wrapper), while runaways ran 203-300 tokens of stray text -> 160 (~640 chars).
# - type_text (dictated text, up to 2,000 chars by schema): ~185 tokens of text, about 130
#   spoken words in one utterance -> 200.
# The configured max_tokens (BEACON_MAX_TOKENS) stays the ceiling: each cap is min(tier, it).
_ACTION_TOOLS = frozenset({"click", "set_value", "read_element", "scroll", "press_key", "open_app",
                           "what_changed", "use_screen", "act_on_screen"})
_ACTION_CAP, _SHORT_TEXT_CAP, _SAY_CAP, _TYPE_TEXT_CAP = 64, 128, 160, 200


def token_cap(tools: Sequence[Mapping[str, Any]], ceiling: int) -> int:
    """max_tokens for a request offering these tools: room for its longest valid answer."""
    names = {str(tool.get("function", {}).get("name", "")) for tool in tools}
    if "type_text" in names:
        cap = _TYPE_TEXT_CAP
    elif "say" in names:
        cap = _SAY_CAP
    elif names <= _ACTION_TOOLS:
        cap = _ACTION_CAP
    else:  # ask_user, done, rewrite, or a tool this table doesn't know: some free text
        cap = _SHORT_TEXT_CAP
    return max(1, min(cap, ceiling))


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
        slots: str = "",
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers={"Authorization": "Bearer beacon"},
            timeout=timeout_s,
            transport=transport,
            trust_env=False,
        )
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens  # ceiling; each request gets token_cap() of it
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
        self._slot_choice = slots.strip().casefold()  # BEACON_LLM_SLOTS; see slot_ids
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

    @property
    def slot_count(self) -> int:
        """Server slots read by discover_model() (1 until then, or when the server doesn't say)."""
        return self._slot_count

    @property
    def slot_ids(self) -> list[int]:
        """The server slots this client may pin (BEACON_LLM_SLOTS).

        "" = all of them; "rest" = all but the last; "last" = only the last; or ids such as "0,1".
        Two Brains on one Bonsai (the live one and a scenario run from scripts/) used to pin
        their families to the same slot ids, so each evicted the other's cached prefix and every
        live step re-read ~3,500 tokens (10 s) and hit its timeout. The live Brain takes "rest",
        the scripts take "last", and neither touches the other's cache.
        """
        every = list(range(self._slot_count))
        choice = self._slot_choice
        if self._slot_count <= 1 or choice in {"", "all"}:
            return every
        if choice == "rest":
            return every[:-1]
        if choice == "last":
            return every[-1:]
        picked = [int(part) for part in re.split(r"[,\s]+", choice) if part.isdigit() and int(part) < self._slot_count]
        return picked or every

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
        ids = self.slot_ids
        if self._slot_count <= 1:
            return None
        system = messages[0].get("content") if messages and messages[0].get("role") == "system" else ""
        names = tuple(str(tool.get("function", {}).get("name", "")) for tool in tools)
        family = (system if isinstance(system, str) else json.dumps(system), names)
        if family in self._slots:
            self._slots.move_to_end(family)
        elif len(self._slots) < len(ids):
            self._slots[family] = ids[len(self._slots)]
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
            "max_tokens": token_cap(tools, self._max_tokens),
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

            body = None
            try:
                body = response.json()
                call = _parse_tool_call(body, allow_text=allow_text, tools=tools)
            except (ValueError, _InvalidToolCall) as exc:
                if attempt == self._attempts - 1:
                    raise BonsaiError("Bonsai did not return one valid tool call") from exc
                payload["messages"].append(
                    {"role": "user", "content": "Reply with exactly one tool call"}
                )
                payload["presence_penalty"] = min(payload["presence_penalty"], 1.0)
                if _was_cut_off(body):
                    # The same cap would cut the retry off too (e.g. a long dictation): lift it.
                    payload["max_tokens"] = max(payload["max_tokens"], self._max_tokens)
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


def _parse_tool_call(body: Any, *, allow_text: bool, tools: Sequence[Mapping[str, Any]] = ()) -> ToolCall:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _InvalidToolCall("Expected one completion choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise _InvalidToolCall("Completion had no message")
    calls = message.get("tool_calls")
    truncated = choices[0].get("finish_reason") == "length"
    if calls is None or calls == []:
        content = message.get("content")
        if has_tool_markup(content):
            # llama-server could not parse a tool call the model wrote in its own syntax
            # ("<tool_call><function=say><parameter=text>..."): read it here, never speak it.
            written = _written_tool_call(content, tools, truncated=truncated)
            if written is not None:
                return written
            raise _InvalidToolCall("The reply held tool-call syntax that did not parse")
    if allow_text and (calls is None or calls == []):
        text = _truncated_answer(content) if truncated else _spoken_text(content)
        if text:
            return ToolCall(f"call_{uuid4().hex}", "say", {"text": text})
    if allow_text and truncated:
        # Cut off inside a say call: keep the complete sentences it got through.
        salvaged = _cut_off_say(calls)
        if salvaged:
            return ToolCall(f"call_{uuid4().hex}", "say", {"text": salvaged})
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


# Tool-call syntax the model sometimes writes as plain text instead of a real tool call:
# Qwen-style XML (<tool_call><function=say><parameter=text>...</parameter></function></tool_call>)
# or <tool_call>{"name": ..., "arguments": {...}}</tool_call>.
_TOOL_MARKUP = re.compile(r"</?tool_call>|<function=[^>]*>|</function>|<parameter=[^>]*>|</parameter>", re.I)


def has_tool_markup(text: Any) -> bool:
    return isinstance(text, str) and _TOOL_MARKUP.search(text) is not None


def strip_tool_markup(text: str) -> str:
    """Text with any tool-call tags removed; a last guard so tool syntax is never shown or spoken."""
    cleaned = _TOOL_MARKUP.sub(" ", text)
    cleaned = re.sub(r"<function=[^\s]*|<parameter=[^\s]*|</?tool_call\b", " ", cleaned)
    return " ".join(cleaned.split())


def _schema_types(tools: Sequence[Mapping[str, Any]], name: str) -> dict[str, str]:
    for tool in tools:
        function = tool.get("function", {})
        if function.get("name") == name:
            properties = function.get("parameters", {}).get("properties", {})
            return {key: str(spec.get("type", "")) for key, spec in properties.items() if isinstance(spec, dict)}
    return {}


def _coerce(value: str, kind: str) -> Any:
    """A written-out parameter value as the type its schema asks for (ids are integers)."""
    if kind == "integer" and re.fullmatch(r"-?\d+", value):
        return int(value)
    if kind == "number" and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return float(value)
    if kind == "boolean" and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    if kind in {"object", "array"}:
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _written_tool_call(content: str, tools: Sequence[Mapping[str, Any]], *, truncated: bool) -> ToolCall | None:
    """The one tool call written out as text in `content`, if it names an offered tool."""
    body = content.split("</think>", 1)[0]
    offered = {str(tool.get("function", {}).get("name", "")) for tool in tools}
    name, arguments = None, {}
    xml = re.search(r"<function=\s*([\w.-]+)\s*>(.*?)(?:</function>|</tool_call>|$)", body, re.S)
    if xml:
        name = xml.group(1)
        types = _schema_types(tools, name)
        for parameter in re.finditer(
                r"<parameter=\s*([\w.-]+)\s*>(.*?)(?=</parameter>|<parameter=|</function>|</tool_call>|$)",
                xml.group(2), re.S):
            key = parameter.group(1)
            arguments[key] = _coerce(parameter.group(2).strip(), types.get(key, "string"))
    else:
        start = re.search(r"<tool_call>\s*", body)
        if start is None:
            return None
        raw = body[start.end():].split("</tool_call>", 1)[0].strip()
        try:
            parsed = json.loads(raw, parse_constant=_reject_json_constant)
        except ValueError:
            match = re.search(r'"name"\s*:\s*"([\w.-]+)".*?"text"\s*:\s*"(.*)', raw, re.S) if truncated else None
            if match is None:
                return None
            parsed = {"name": match.group(1), "arguments": {"text": _unescape_partial_json(match.group(2))}}
        if not isinstance(parsed, dict) or not isinstance(parsed.get("arguments", {}), dict):
            return None
        name, arguments = parsed.get("name"), dict(parsed.get("arguments") or {})
    if not isinstance(name, str) or (offered and name not in offered):
        return None
    for key, value in list(arguments.items()):
        if isinstance(value, str):
            value = strip_tool_markup(value)
            arguments[key] = _complete_sentences(value) if truncated and key == "text" else value
    if name == "say" and not str(arguments.get("text", "")).strip():
        return None
    return ToolCall(f"call_{uuid4().hex}", name, arguments)


def _spoken_text(content: Any) -> str:
    """The answer in a plain-text reply: its first paragraph, without a written-out say call."""
    if not isinstance(content, str):
        return ""
    first = content.split("</think>", 1)[0].strip().split("\n\n", 1)[0].strip()
    match = re.fullmatch(r'say\s*[:(]?\s*"(.+)"\s*\)?', first, re.DOTALL)
    return (match.group(1) if match else first).strip()


def _was_cut_off(body: Any) -> bool:
    try:
        return body["choices"][0]["finish_reason"] == "length"
    except (KeyError, IndexError, TypeError):
        return False


def _truncated_answer(content: Any) -> str:
    """A plain-text answer cut off by max_tokens, trimmed to its last complete sentence.

    If its first paragraph finished before the cut, that paragraph is the answer as usual.
    """
    if not isinstance(content, str):
        return ""
    body = content.split("</think>", 1)[0].strip()
    if "\n\n" in body:
        return _spoken_text(body)
    # A cut-off written-out say call or raw tool-call JSON: keep only the text being spoken.
    match = (re.search(r'"text"\s*:\s*"(.*)', body, re.DOTALL)
             or re.fullmatch(r'say\s*[:(]?\s*"(.*)', body, re.DOTALL))
    if match:
        body = _unescape_partial_json(match.group(1))
    return _complete_sentences(body)


def _cut_off_say(calls: Any) -> str:
    """The spoken text of a lone say call whose JSON arguments max_tokens cut off."""
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return ""
    function = calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != "say":
        return ""
    raw = function.get("arguments")
    if not isinstance(raw, str):
        return ""
    try:
        json.loads(raw)
        return ""  # the arguments parsed: the normal path handles (or rejects) them
    except ValueError:
        match = re.search(r'"text"\s*:\s*"(.*)', raw, re.DOTALL)
        return _complete_sentences(_unescape_partial_json(match.group(1))) if match else ""


def _unescape_partial_json(fragment: str) -> str:
    """Decode a JSON string body that may stop anywhere, dropping a closing quote and the rest."""
    text = []
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if char == '"':
            break
        if char == "\\":
            if index + 1 >= len(fragment):
                break
            following = fragment[index + 1]
            if following == "u":
                code = fragment[index + 2:index + 6]
                if len(code) < 4:
                    break
                try:
                    text.append(chr(int(code, 16)))
                except ValueError:
                    break
                index += 6
                continue
            text.append({"n": " ", "t": " ", "r": " "}.get(following, following))
            index += 2
            continue
        text.append(char)
        index += 1
    return "".join(text)


def _complete_sentences(text: str) -> str:
    """Text up to its last sentence end (. ! ?, optionally followed by a closing quote/bracket)."""
    text = text.strip()
    ends = [match.end() for match in re.finditer(r"""[.!?]+["')\]]?(?=\s|$)""", text)]
    return text[:ends[-1]].strip() if ends else ""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")
