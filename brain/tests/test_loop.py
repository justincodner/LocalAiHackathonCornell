import asyncio
import json
from pathlib import Path

import pytest

from beacon_brain.config import Config
from beacon_brain.helper_client import HelperError
from beacon_brain.llm import BonsaiError, ToolCall
from beacon_brain.loop import Brain


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = iter(calls)
        self.requests = []

    async def next_tool_call(self, messages, tools):
        self.requests.append(messages[-1]["content"])
        return next(self.calls)


class ReplayHelper:
    def __init__(self, snapshot):
        self.screen = snapshot
        self.spoken = []
        self.hud_states = []
        self.actions = []
        self.brain = None
        self.listening = asyncio.Event()

    async def snapshot(self):
        return self.screen

    async def hud(self, state, **fields):
        self.hud_states.append(state)

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        return speech_id

    async def request(self, path, payload):
        self.actions.append((path, payload))
        return {"ok": True}

    async def listen(self, mode):
        if mode == "start":
            self.listening.set()


def fixture(name):
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "mock_helper" / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.loop
def test_screen_summary_speaks_and_logs(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = ScriptedModel(ToolCall("test-1", "say", {"text": "Mail inbox. Sarah wrote about lunch."}))
        path = tmp_path / "session.jsonl"
        brain = helper.brain = Brain(helper, model, Config(log_path=path), "Test instruction")

        await brain.on_event({"type": "stt.final", "text": "What is on my screen?", "stt_ms": 100})
        await brain.active

        assert helper.spoken == ["Mail inbox. Sarah wrote about lunch."]
        assert helper.hud_states == ["thinking", "speaking", "idle"]
        assert helper.screen["text"] in model.requests[0]
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["tool"] == "say"
        assert record["valid"] and record["ok"]
        assert record["ms"]["stt"] == 100

    asyncio.run(run())


@pytest.mark.loop
def test_confirmation_cannot_execute_against_changed_screen(tmp_path):
    async def run():
        screen = fixture("mail_reply_draft")
        target = next(item["id"] for item in screen["elements"] if item["label"] == "Send")
        helper = ReplayHelper(screen)
        model = ScriptedModel(
            ToolCall("test-1", "click", {"id": target}),
            ToolCall("test-2", "say", {"text": "The screen changed."}),
        )
        config = Config(log_path=tmp_path / "session.jsonl", settle_ms=0)
        brain = helper.brain = Brain(helper, model, config, "Test instruction")

        await brain.on_event({"type": "stt.final", "text": "Send the message"})
        await asyncio.wait_for(helper.listening.wait(), timeout=2)
        helper.screen = fixture("mail_inbox")
        await brain.on_event({"type": "app.changed"})
        await brain.on_event({"type": "stt.final", "text": "yes"})
        await brain.active

        assert helper.actions == []
        assert helper.spoken[-1] == "The screen changed."

    asyncio.run(run())


class StaleOnceHelper(ReplayHelper):
    async def request(self, path, payload):
        if not self.actions:
            self.actions.append((path, payload))
            raise HelperError("stale_snapshot", "expired")
        return await super().request(path, payload)


class SlowModel(ScriptedModel):
    async def next_tool_call(self, messages, tools):
        await asyncio.sleep(0.15)
        return await super().next_tool_call(messages, tools)


@pytest.mark.loop
def test_slow_model_gets_a_spoken_filler(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = SlowModel(ToolCall("a", "say", {"text": "Mail inbox."}))
        config = Config(log_path=tmp_path / "s.jsonl", thinking_filler_ms=50)
        brain = helper.brain = Brain(helper, model, config, "x")
        await brain.on_event({"type": "stt.final", "text": "what is on my screen"})
        await brain.active
        assert helper.spoken == ["One moment.", "Mail inbox."]

    asyncio.run(run())


@pytest.mark.loop
def test_done_cannot_claim_a_send_that_never_happened(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_reply_draft"))
        model = ScriptedModel(
            ToolCall("a", "type_text", {"text": "I'll be ten minutes late", "id": 4}),
            ToolCall("b", "done", {"summary": "Sent a message to Sarah Chen."}),
        )
        config = Config(log_path=tmp_path / "s.jsonl", settle_ms=0)
        brain = helper.brain = Brain(helper, model, config, "x")
        await brain.on_event({"type": "stt.final", "text": "type I'll be ten minutes late"})
        await brain.active
        assert helper.spoken[-1] == "I typed: I'll be ten minutes late. Nothing has been sent yet."

    asyncio.run(run())


@pytest.mark.loop
def test_ambiguous_click_asks_then_presses_what_the_user_chose(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = ScriptedModel(
            ToolCall("a", "click", {"id": 2}),  # the 2B guesses New Message
            ToolCall("b", "click", {"id": 3}),
            ToolCall("c", "done", {"summary": "Opened a reply."}),
        )
        config = Config(log_path=tmp_path / "s.jsonl", settle_ms=0)
        brain = helper.brain = Brain(helper, model, config, "x")
        await brain.on_event({"type": "stt.final", "text": "click the button"})
        await asyncio.wait_for(helper.listening.wait(), timeout=2)
        assert helper.actions == []
        assert helper.spoken[0].startswith("Which button: New Message, Reply")
        await brain.on_event({"type": "stt.final", "text": "the reply one"})
        await brain.active
        assert [payload["element_id"] for _, payload in helper.actions] == [3]
        steps = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        assert steps[0]["tool"] == "ask_user" and steps[0]["grounded"] == "asked"
        # The model's next prompt kept the original request alongside the answer (the live bug).
        assert "REQUEST: click the button\nUSER ANSWERED" in model.requests[1]
        assert model.requests[1].rstrip().endswith("the reply one")

    asyncio.run(run())


@pytest.mark.loop
@pytest.mark.parametrize("summary, label, expected_prompts", [
    ("Send to Sarah Chen: I'll be ten minutes late.", "Send", 1),  # model's confirm covers Send: ask once
    ("Send to Sarah Chen: I'll be ten minutes late.", "Delete", 2),  # never covers Delete: guard asks again
])
def test_model_confirm_covers_only_the_same_commitment(tmp_path, summary, label, expected_prompts):
    async def run():
        screen = fixture("mail_reply_draft")
        screen["elements"] = [dict(item, label=label) if item["label"] == "Send" else item
                              for item in screen["elements"]]
        target = next(item["id"] for item in screen["elements"] if item["label"] == label)
        helper = ReplayHelper(screen)
        model = ScriptedModel(
            ToolCall("a", "confirm", {"summary": summary}),
            ToolCall("b", "click", {"id": target}),
            ToolCall("c", "done", {"summary": "Done."}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", settle_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": "send it"})
        for _ in range(expected_prompts):
            await asyncio.wait_for(helper.listening.wait(), timeout=2)
            helper.listening.clear()
            await brain.on_event({"type": "stt.final", "text": "yes"})
        await asyncio.wait_for(brain.active, timeout=3)
        prompts = [text for text in helper.spoken if text.endswith("Say yes to continue.")]
        assert len(prompts) == expected_prompts
        assert len(helper.actions) == 1

    asyncio.run(run())


class FailingModel:
    async def next_tool_call(self, messages, tools):
        raise BonsaiError("timed out twice")


@pytest.mark.loop
def test_stale_snapshot_retries_the_same_call_once(tmp_path):
    async def run():
        helper = StaleOnceHelper(fixture("mail_inbox"))
        model = ScriptedModel(
            ToolCall("test-1", "click", {"id": 3}),
            ToolCall("test-2", "done", {"summary": "Reply opened."}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", settle_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": "click reply"})
        await brain.active
        assert [path for path, _ in helper.actions] == ["/ax/act", "/ax/act"]
        assert helper.spoken[-1] == "Reply opened."

    asyncio.run(run())


@pytest.mark.loop
def test_two_invalid_calls_end_with_an_apology(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = ScriptedModel(ToolCall("a", "click", {"id": 99}), ToolCall("b", "click", {"id": 98}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "x")
        await brain.on_event({"type": "stt.final", "text": "click the thing"})
        await brain.active
        assert helper.actions == []
        assert helper.spoken == ["I couldn't find a valid action for that request."]
        assert len(model.requests) == 2

    asyncio.run(run())


@pytest.mark.loop
def test_model_failure_is_spoken(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        brain = helper.brain = Brain(helper, FailingModel(), Config(log_path=tmp_path / "s.jsonl"), "x")
        await brain.on_event({"type": "stt.final", "text": "what is on my screen"})
        await brain.active
        assert helper.spoken == ["I'm having trouble thinking. Please try again."]
        assert helper.hud_states[-1] == "idle"

    asyncio.run(run())


@pytest.mark.loop
def test_refused_action_never_reaches_the_helper(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        helper.screen = dict(helper.screen, app="Terminal")
        model = ScriptedModel(ToolCall("a", "click", {"id": 3}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "x")
        await brain.on_event({"type": "stt.final", "text": "click reply"})
        await brain.active
        assert helper.actions == []
        assert helper.spoken == ["I can't do that one for you."]

    asyncio.run(run())


@pytest.mark.loop
def test_barge_in_cancels_the_running_task(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = ScriptedModel(ToolCall("a", "ask_user", {"question": "Which message?"}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "x")
        await brain.on_event({"type": "stt.final", "text": "open it"})
        await asyncio.wait_for(helper.listening.wait(), timeout=2)
        await brain.on_event({"type": "barge_in"})
        assert brain.active.done() and brain.state == "idle"

    asyncio.run(run())


@pytest.mark.loop
@pytest.mark.parametrize("answer, expected_presses", [("no", 0), ("yes", 1)])
def test_send_waits_for_confirmation(tmp_path, answer, expected_presses):
    async def run():
        screen = fixture("mail_reply_draft")
        target = next(item["id"] for item in screen["elements"] if item["label"] == "Send")
        helper = ReplayHelper(screen)
        model = ScriptedModel(
            ToolCall("test-1", "click", {"id": target}),
            ToolCall("test-2", "done", {"summary": "Sent."}),
        )
        config = Config(log_path=tmp_path / "session.jsonl", settle_ms=0)
        brain = helper.brain = Brain(helper, model, config, "Test instruction")

        await brain.on_event({"type": "stt.final", "text": "Send the message"})
        await asyncio.wait_for(helper.listening.wait(), timeout=2)
        assert helper.actions == []
        await brain.on_event({"type": "stt.final", "text": answer})
        await brain.active

        assert len(helper.actions) == expected_presses
        assert "Say yes to continue" in helper.spoken[0]

    asyncio.run(run())
