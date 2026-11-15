"""B1: Bonsai client request shape, prewarm and fallbacks, against a fake llama-server."""

import asyncio
import json

import httpx
import pytest

from beacon_brain.helper_client import HelperClient
from beacon_brain.llm import BonsaiClient, BonsaiError
from beacon_brain.tools import TOOL_DEFINITIONS


def server(handler, slots=None):
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json={"total_slots": slots}) if slots else httpx.Response(404)
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


def test_a_single_attempt_client_does_not_retry():
    transport, seen = server(lambda body, n: httpx.Response(500, text="busy"))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8082/v1", transport=transport, attempts=1) as client:
            await client.next_tool_call([{"role": "user", "content": "x"}], TOOL_DEFINITIONS)

    with pytest.raises(BonsaiError):
        asyncio.run(run())
    assert len(seen) == 1


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


def test_plain_text_answer_becomes_say_when_say_is_offered():
    looping = 'The capital of France is Paris.\n\nsay "The capital of France is Paris."\n'
    transport, seen = server(lambda body, n: httpx.Response(200, json={"choices": [{"message": {"content": looping}}]}))
    say_only = [tool for tool in TOOL_DEFINITIONS if tool["function"]["name"] == "say"]

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], say_only)

    call = asyncio.run(run())
    assert (call.name, call.arguments) == ("say", {"text": "The capital of France is Paris."})
    assert len(seen) == 1 and seen[0]["stop"] == ["</think>"]


def test_startup_prewarms_every_prompt_family_and_survives_a_failure():
    from beacon_brain.config import Config
    from beacon_brain.loop import SCREEN_SUMMARY_TOOLS, prompt_families
    from beacon_brain.main import prewarm

    warmed = []

    class Model:
        async def prewarm(self, system, family_tools):
            warmed.append((system, [tool["function"]["name"] for tool in family_tools]))
            if len(warmed) == 2:
                raise BonsaiError("busy")
            return 1.0

    asyncio.run(prewarm(Config(), Model(), "MAIN"))
    families = prompt_families("MAIN")
    assert len(warmed) == len(families) == len({(s, tuple(t)) for s, t in warmed})
    assert warmed[0] == ("MAIN", [tool["function"]["name"] for tool in TOOL_DEFINITIONS])
    assert ("MAIN", ["say"]) in warmed and SCREEN_SUMMARY_TOOLS[0]["function"]["name"] == "say"


def test_a_one_slot_server_gets_no_slot_pin():
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}), slots=1)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            await client.next_tool_call([{"role": "system", "content": "A"}], TOOL_DEFINITIONS)

    asyncio.run(run())
    assert "id_slot" not in seen[0]


def test_prewarmed_families_keep_their_slots_and_others_share_the_rest():
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}), slots=3)
    say_only = [tool for tool in TOOL_DEFINITIONS if tool["function"]["name"] == "say"]

    def ask(client, system, tools):
        return client.next_tool_call(
            [{"role": "system", "content": system}, {"role": "user", "content": "x"}], tools)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            await client.prewarm("MAIN", TOOL_DEFINITIONS)
            await client.prewarm("MAIN", say_only)  # same prompt, other tools: another family
            await ask(client, "MAIN", TOOL_DEFINITIONS)
            await ask(client, "ADVICE", say_only)  # not warmed: the spare slot
            await ask(client, "DECIDE", say_only)  # takes the spare slot over
            await ask(client, "MAIN", say_only)
            await ask(client, "ADVICE", say_only)

    asyncio.run(run())
    assert [body["id_slot"] for body in seen] == [0, 1, 0, 2, 2, 1, 2]


def test_with_every_slot_warm_another_family_is_left_to_the_server():
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}), slots=1 + 1)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            await client.prewarm("A", TOOL_DEFINITIONS)
            await client.prewarm("B", TOOL_DEFINITIONS)
            await client.next_tool_call([{"role": "system", "content": "C"}], TOOL_DEFINITIONS)

    asyncio.run(run())
    assert [body.get("id_slot") for body in seen] == [0, 1, None]
