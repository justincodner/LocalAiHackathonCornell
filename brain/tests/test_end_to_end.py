"""B3 acceptance over real sockets: Mock Helper (HTTP + WebSocket) -> Brain -> scripted model."""

import asyncio
import json
import re
import socket

import pytest
import uvicorn

from beacon_brain.config import Config
from beacon_brain.helper_client import HelperClient
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain
from mock_helper.server import create_app


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.screens = []

    async def next_tool_call(self, messages, tools):
        self.screens.append(messages[-1]["content"])
        return self.calls.pop(0)


async def run_session(model, utterances, log_path, *, replies=()):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    helper = HelperClient(f"http://127.0.0.1:{port}")
    brain = Brain(helper, model, Config(log_path=log_path, settle_ms=0, reply_timeout_s=5), "SYSTEM")
    events = []

    async def pump():
        async for event in helper.events():
            events.append(event["type"])
            await brain.on_event(event)

    pumping = asyncio.create_task(pump())
    try:
        await asyncio.sleep(0.2)
        pending_replies = list(replies)
        for text in utterances:
            await helper.request("/debug/utterance", {"text": text})
            while brain.active is None or not brain.active.done():
                if brain.reply is not None and not brain.reply.done() and pending_replies:
                    await helper.request("/debug/utterance", {"text": pending_replies.pop(0)})
                await asyncio.sleep(0.05)
        final_screen = await helper.snapshot()
    finally:
        pumping.cancel()
        await helper.aclose()
        server.should_exit = True
        await serving
    return events, final_screen


@pytest.mark.loop
def test_what_is_on_my_screen_produces_say_and_a_log_line(tmp_path):
    model = ScriptedModel(ToolCall("c1", "say", {"text": "Mail inbox. Newest is from Sarah Chen."}))
    log = tmp_path / "session.jsonl"
    events = asyncio.run(asyncio.wait_for(run_session(model, ["what is on my screen"], log), 20))[0]

    assert "APP Mail" in model.screens[0] and "REQUEST: what is on my screen" in model.screens[0]
    assert {"stt.final", "tts.started", "tts.finished"} <= set(events)
    record = json.loads(log.read_text(encoding="utf-8"))
    assert (record["tool"], record["valid"], record["risk"], record["ok"]) == ("say", True, "allow", True)
    assert {"stt", "snapshot", "llm", "act"} <= record["ms"].keys()


class LookupModel(ScriptedModel):
    """Scripted steps that pick elements by label from the SCREEN text, like the model would."""

    async def next_tool_call(self, messages, tools):
        call = await super().next_tool_call(messages, tools)
        label = call.arguments.get("id")
        if isinstance(label, str):
            screen = self.screens[-1]
            element_id = int(re.search(rf'\[(\d+)\] button "{re.escape(label)}"', screen).group(1))
            call = ToolCall(call.id, call.name, {**call.arguments, "id": element_id})
        return call


@pytest.mark.loop
def test_reply_to_sarah_with_spoken_confirmation(tmp_path):
    """Story A4 against the mock (Justin's recorded Mail screens when present): reply, type, confirm, send."""
    model = LookupModel(
        ToolCall("c1", "click", {"id": "Reply"}),
        ToolCall("c2", "type_text", {"text": "I'll be ten minutes late"}),
        ToolCall("c3", "click", {"id": "Send"}),
        ToolCall("c4", "done", {"summary": "Sent to Sarah Chen."}),
    )
    log = tmp_path / "session.jsonl"
    events, final_screen = asyncio.run(asyncio.wait_for(
        run_session(model, ["reply to Sarah that I'll be ten minutes late"], log, replies=["send it"]), 20,
    ))

    assert "APP Mail | WINDOW \"Re: Lunch tomorrow?\"" in model.screens[1]
    assert "I'll be ten minutes late" in model.screens[2]
    assert final_screen["window"] != "Re: Lunch tomorrow?"  # sent: back to the inbox
    steps = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(step["tool"], step["risk"], step["ok"]) for step in steps] == [
        ("click", "allow", True), ("type_text", "allow", True), ("click", "confirm", True), ("done", "allow", True),
    ]
    assert "app.changed" in events
