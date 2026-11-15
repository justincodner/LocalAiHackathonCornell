"""Questions about a screen region the user selected (stt.final with `focus`, contracts/events.md)."""

import asyncio
import json

import pytest

from beacon_brain import questions
from beacon_brain.config import Config
from beacon_brain.llm import BonsaiError, ToolCall
from beacon_brain.loop import Brain

from test_loop import ReplayHelper, ScriptedModel, fixture


INVOICE = "Invoice 42\nDue December 1\nTotal  $118.00\n\nControls in the selection:\n- button Pay now"


def selection_event(text, focus_text=INVOICE, **extra):
    focus = {"text": focus_text, "source": "selection", "app": "Safari", "bounds": [10, 20, 300, 120]}
    return {"type": "stt.final", "utterance_id": "u_0001", "text": text, "stt_ms": 50, "focus": focus, **extra}


class RecordingModel(ScriptedModel):
    """Keeps every full message list, so tests can check the system prompt and context."""

    def __init__(self, *calls):
        super().__init__(*calls)
        self.messages = []

    async def next_tool_call(self, messages, tools):
        self.messages.append([dict(m) for m in messages])
        return await super().next_tool_call(messages, tools)


@pytest.mark.loop
def test_question_about_a_selection_uses_the_selected_text_and_no_snapshot(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(ToolCall("a", "say", {"text": "The total is $118.00, due December 1."}))
        path = tmp_path / "s.jsonl"
        brain = helper.brain = Brain(helper, model, Config(log_path=path), "Test instruction")

        await brain.on_event(selection_event("What do I owe?"))
        await brain.active

        assert helper.snapshot_calls == 0, "the selection is the context, not the whole window"
        assert helper.spoken == ["The total is $118.00, due December 1."]
        assert helper.hud_states == ["thinking", "speaking", "idle"]
        system, user = model.messages[0]
        assert system["content"] == questions.SELECTION_PROMPT
        assert "SELECTED (the part of the screen the user selected in Safari)" in user["content"]
        assert INVOICE in user["content"]
        assert user["content"].rstrip().endswith("QUESTION: What do I owe?")
        assert model.tools == [["say"]]
        record = json.loads(path.read_text())
        assert record["kind"] == "selection"
        assert record["snapshot_id"] is None
        assert record["focus_chars"] == len(INVOICE)
        assert record["ok"]

    asyncio.run(run())


@pytest.mark.loop
def test_an_action_worded_request_with_a_selection_is_still_a_question(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(ToolCall("a", "say", {"text": "It's an invoice for $118.00."}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")

        await brain.on_event(selection_event("summarize this"))
        await brain.active

        assert helper.actions == []
        assert helper.snapshot_calls == 0
        assert helper.spoken == ["It's an invoice for $118.00."]

    asyncio.run(run())


@pytest.mark.loop
def test_echoed_question_is_retried_then_falls_back(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(ToolCall("a", "say", {"text": "What do I owe?"}),
                               ToolCall("b", "say", {"text": "Is it due?"}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")

        await brain.on_event(selection_event("What do I owe?"))
        await brain.active

        assert len(model.messages) == 2
        assert questions.SELECTION_RETRY in model.messages[1][0]["content"]
        assert helper.spoken == [questions.SELECTION_FALLBACK]

    asyncio.run(run())


@pytest.mark.loop
def test_read_this_speaks_the_selection_without_a_model_call(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel()
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")

        await brain.on_event(selection_event("Read this out loud.", focus_text="Meeting moved to 3 pm."))
        await brain.active

        assert model.messages == []
        assert helper.spoken == ["Meeting moved to 3 pm."]

    asyncio.run(run())


@pytest.mark.loop
def test_selection_text_is_bounded(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(ToolCall("a", "say", {"text": "It is a long list of x."}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")

        await brain.on_event(selection_event("What is this?", focus_text="x" * 10_000))
        await brain.active

        user = model.messages[0][1]["content"]
        selected = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
        assert len(selected) == questions.SELECTION_MAX_CHARS
        assert selected.endswith("…")

    asyncio.run(run())


@pytest.mark.loop
def test_selection_question_starts_a_new_request_while_beacon_waits_for_an_answer(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(ToolCall("a", "say", {"text": "The total is $118.00."}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
        pending = asyncio.get_running_loop().create_future()
        brain.reply = pending  # Beacon asked something and is waiting

        await brain.on_event(selection_event("What do I owe?"))
        await brain.active

        assert not pending.done(), "the selection question is not the answer to the old question"
        assert helper.spoken == ["The total is $118.00."]

    asyncio.run(run())


@pytest.mark.loop
def test_model_failure_on_a_selection_is_spoken(tmp_path):
    class FailingModel:
        async def next_tool_call(self, messages, tools):
            raise BonsaiError("down")

    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        brain = helper.brain = Brain(helper, FailingModel(), Config(log_path=tmp_path / "s.jsonl"), "Test instruction")

        await brain.on_event(selection_event("What do I owe?"))
        await brain.active

        assert helper.spoken == ["I'm having trouble thinking. Please try again."]
        assert helper.hud_states[-1] == "idle"

    asyncio.run(run())


@pytest.mark.loop
@pytest.mark.parametrize("focus", [None, "not a dict", {"text": "   "}, {"source": "selection"}])
def test_missing_or_empty_focus_is_an_ordinary_utterance(tmp_path, focus):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        answer = "The capital of Germany is Berlin."
        model = RecordingModel(ToolCall("a", "say", {"text": answer}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
        event = {"type": "stt.final", "text": "What is the capital of Germany?"}
        if focus is not None:
            event["focus"] = focus

        await brain.on_event(event)
        await brain.active

        assert model.messages[0][0]["content"] == questions.ROUTING_PROMPT
        assert helper.spoken == [answer]

    asyncio.run(run())


@pytest.mark.parametrize("request_text, expected", [
    ("Read this", True),
    ("read it out loud.", True),
    ("Please read the selection", True),
    ("read", True),
    ("Read this email and reply", False),
    ("What does this say?", False),
    ("summarize this", False),
])
def test_wants_selection_read(request_text, expected):
    assert questions.wants_selection_read(request_text) is expected
