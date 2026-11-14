"""B1: Bonsai client request shape, prewarm and fallbacks, against a fake llama-server."""

import asyncio
import json

import httpx
import pytest

from beacon_brain.helper_client import HelperClient
from beacon_brain.llm import BonsaiClient, BonsaiError
from beacon_brain.tools import TOOL_DEFINITIONS


def server(handler):
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Ternary-Bonsai-2-2B-PQ2_0.gguf"}]})
        body = json.loads(request.content)
        seen.append(body)
        return handler(body, len(seen))

    return httpx.MockTransport(respond), seen


def tool_reply(name, arguments):
    return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
        "id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}}]})


def test_request_turns_thinking_off_and_requires_a_tool():
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], TOOL_DEFINITIONS)

    call = asyncio.run(run())
    assert (call.name, call.arguments) == ("say", {"text": "hi"})
    body = seen[0]
    assert body["tool_choice"] == "required" and body["cache_prompt"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["model"] == "Ternary-Bonsai-2-2B-PQ2_0.gguf"


def test_prewarm_sends_the_fixed_prefix_with_one_token():
    transport, seen = server(lambda body, n: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.prewarm("SYSTEM PROMPT", TOOL_DEFINITIONS)

    assert asyncio.run(run()) >= 0
    body = seen[0]
    assert body["max_tokens"] == 1 and body["cache_prompt"] is True
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT"}
    assert body["tools"] == TOOL_DEFINITIONS


def test_prewarm_failure_is_a_bonsai_error():
    transport, _ = server(lambda body, n: httpx.Response(500, text="boom"))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            await client.prewarm("S", TOOL_DEFINITIONS)

    with pytest.raises(BonsaiError):
        asyncio.run(run())


def test_malformed_arguments_are_retried_once():
    def handler(body, n):
        if n == 1:
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
                "id": "c1", "function": {"name": "click", "arguments": "{id: 3"}}]}}]})
        return tool_reply("click", {"id": 3})

    transport, seen = server(handler)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], TOOL_DEFINITIONS)

    assert asyncio.run(run()).arguments == {"id": 3}
    assert seen[1]["messages"][-1]["content"] == "Reply with exactly one tool call"
    assert seen[1]["presence_penalty"] == 1.0


def test_snapshot_asks_for_the_configured_element_cap():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "snapshot_id": "s_1"})

    async def run():
        helper = HelperClient("http://127.0.0.1:8765", transport=httpx.MockTransport(respond), max_elements=80)
        await helper.snapshot()
        await helper.aclose()

    asyncio.run(run())
    assert requests == [{"scope": "window", "max_elements": 80}]
