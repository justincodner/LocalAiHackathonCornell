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
        slot_count = 8
        slot_ids = list(range(8))

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


def tools_named(*names):
    from beacon_brain.followup import REWRITE_TOOLS
    from beacon_brain.questions import ROUTE_TOOLS
    every = TOOL_DEFINITIONS + ROUTE_TOOLS + REWRITE_TOOLS
    return [tool for tool in every if tool["function"]["name"] in names]


@pytest.mark.parametrize(("names", "cap"), [
    (("click", "set_value"), 64),  # DECIDE_TOOLS
    (("rewrite",), 128),  # follow-up rewrites
    (("ask_user", "done"), 128),
    (("say",), 160),  # screen summaries and screen questions
    (("say", "use_screen", "act_on_screen"), 160),  # question routing
    (tuple(tool["function"]["name"] for tool in TOOL_DEFINITIONS), 200),  # the main loop
])
def test_max_tokens_follows_the_offered_tools(names, cap):
    transport, seen = server(lambda body, n: tool_reply(body["tools"][0]["function"]["name"], {}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            await client.complete([{"role": "user", "content": "x"}], tools_named(*names))

    asyncio.run(run())
    assert seen[0]["max_tokens"] == cap


def test_configured_max_tokens_is_the_ceiling(monkeypatch):
    from beacon_brain.config import Config

    monkeypatch.setenv("BEACON_MAX_TOKENS", "100")
    ceiling = Config.from_environment().max_tokens
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport, max_tokens=ceiling) as client:
            await client.complete([{"role": "user", "content": "x"}], tools_named("say"))
            await client.complete([{"role": "user", "content": "x"}], tools_named("click"))

    asyncio.run(run())
    assert [body["max_tokens"] for body in seen] == [100, 64]


def text_reply(content, finish_reason="length"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]})


def ask_once(reply, names=("say",)):
    transport, seen = server(lambda body, n: reply)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], tools_named(*names))

    return asyncio.run(run()), seen


def test_cut_off_plain_text_is_spoken_up_to_its_last_full_sentence():
    call, seen = ask_once(text_reply("The page lists three emails. The first is from Sarah Chen! The second is ab"))
    assert (call.name, call.arguments) == ("say", {"text": "The page lists three emails. The first is from Sarah Chen!"})
    assert len(seen) == 1  # no second request


def test_cut_off_written_out_say_and_tool_json_keep_only_the_spoken_text():
    call, _ = ask_once(text_reply('say "Your inbox has two unread emails. One is from Sa'))
    assert call.arguments == {"text": "Your inbox has two unread emails."}
    call, _ = ask_once(text_reply('<tool_call>\n{"name": "say", "arguments": {"text": "It says \\"hi\\". Then a lo'))
    assert call.arguments == {"text": 'It says "hi".'}


def test_cut_off_say_arguments_keep_their_full_sentences():
    reply = httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"tool_calls": [{
        "id": "c1", "function": {"name": "say", "arguments": '{"text": "Two buttons are shown. Submit is at the bot'},
    }]}}]})
    call, seen = ask_once(reply, names=tuple(tool["function"]["name"] for tool in TOOL_DEFINITIONS))
    assert (call.name, call.arguments) == ("say", {"text": "Two buttons are shown."})
    assert len(seen) == 1


def test_a_cut_off_fragment_with_no_full_sentence_is_asked_again():
    transport, seen = server(lambda body, n: text_reply("The page shows a long list of") if n == 1
                             else tool_reply("say", {"text": "A list of links."}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], tools_named("say"))

    assert asyncio.run(run()).arguments == {"text": "A list of links."}
    assert len(seen) == 2 and seen[1]["messages"][-1]["content"] == "Reply with exactly one tool call"


def test_cut_off_text_without_say_on_offer_is_still_retried():
    transport, seen = server(lambda body, n: text_reply("I should click Submit. Then") if n == 1
                             else tool_reply("click", {"id": 3}))

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], tools_named("click", "set_value"))

    assert asyncio.run(run()).name == "click"
    assert len(seen) == 2


def test_an_unfinished_reply_that_stopped_normally_keeps_its_old_handling():
    call, _ = ask_once(text_reply("The page shows a long list of", finish_reason="stop"))
    assert call.arguments == {"text": "The page shows a long list of"}


def test_a_retry_after_a_cut_off_reply_gets_the_full_ceiling():
    def handler(body, n):
        if n == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "type_text", "arguments": '{"id": 3, "text": "a long dictat'},
            }]}}]})
        return tool_reply("type_text", {"id": 3, "text": "a long dictation"})

    transport, seen = server(handler)

    async def run():
        async with BonsaiClient("http://127.0.0.1:8080/v1", transport=transport, max_tokens=300) as client:
            return await client.next_tool_call([{"role": "user", "content": "x"}], TOOL_DEFINITIONS)

    assert asyncio.run(run()).name == "type_text"
    assert [body["max_tokens"] for body in seen] == [200, 300]


def test_a_one_slot_server_warms_only_the_action_prompt():
    from beacon_brain.config import Config
    from beacon_brain.main import prewarm

    warmed = []

    class Model:
        slot_count = 1

        async def prewarm(self, system, family_tools):
            warmed.append(system)
            return 1.0

    asyncio.run(prewarm(Config(), Model(), "MAIN"))
    assert warmed == ["MAIN"]


def test_slot_choice_keeps_live_and_scenario_brains_apart():
    for choice, expected in [("", [0, 1, 2, 3]), ("rest", [0, 1, 2]), ("last", [3]), ("1, 2", [1, 2]), ("9", [0, 1, 2, 3])]:
        client = BonsaiClient("http://bonsai.test/v1", slots=choice)
        client._slot_count = 4
        assert client.slot_ids == expected, choice
    one = BonsaiClient("http://bonsai.test/v1", slots="rest")
    assert one.slot_ids == [0]  # a one-slot server is never left with none


def test_a_last_slot_client_pins_every_family_to_the_last_slot_only():
    transport, seen = server(lambda body, n: tool_reply("say", {"text": "hi"}), slots=4)
    client = BonsaiClient("http://bonsai.test/v1", transport=transport, slots="last")

    async def run():
        await client.discover_model()
        for system in ("A", "B"):
            await client.next_tool_call([{"role": "system", "content": system}, {"role": "user", "content": "x"}],
                                         TOOL_DEFINITIONS)

    asyncio.run(run())
    assert [body.get("id_slot") for body in seen if "messages" in body] == [3, 3]


def test_prewarm_warms_only_as_many_families_as_the_brain_may_pin():
    from beacon_brain.config import Config
    from beacon_brain.main import prewarm

    warmed = []

    class Model:
        slot_count = 4
        slot_ids = [0, 1, 2]

        async def prewarm(self, system, family_tools):
            warmed.append(system)
            return 1.0

    asyncio.run(prewarm(Config(), Model(), "MAIN"))
    assert len(warmed) == 3 and warmed[0] == "MAIN"
