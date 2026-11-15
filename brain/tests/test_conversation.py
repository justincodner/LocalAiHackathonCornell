"""Conversation memory across requests: follow-ups resolve, standalone requests pay nothing."""

import asyncio
import json

import pytest

from beacon_brain import followup
from beacon_brain.config import Config
from beacon_brain.llm import BonsaiError, ToolCall
from beacon_brain.loop import Brain
from beacon_brain.memory import Conversation

from test_loop import ReplayHelper, fixture


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordingModel:
    """Scripted tool calls; keeps every full message list and tool list it was sent."""

    def __init__(self, *calls):
        self.calls = list(calls)
        self.messages = []
        self.tools = []

    async def next_tool_call(self, messages, tools):
        self.messages.append([dict(message) for message in messages])
        self.tools.append([tool["function"]["name"] for tool in tools])
        call = self.calls.pop(0)
        if isinstance(call, Exception):
            raise call
        return call

    @property
    def last_words(self):
        return [messages[-1]["content"] for messages in self.messages]


def say(text):
    return ToolCall("s", "say", {"text": text})


def rewrite(text):
    return ToolCall("r", "rewrite", {"request": text})


def brain_for(helper, model, tmp_path, **config):
    brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", **config), "Test instruction")
    return brain


async def hear(brain, text):
    await brain.on_event({"type": "stt.final", "text": text})
    await brain.active


# --- Conversation: a fixed budget however long the session runs ---

def test_conversation_fades_older_replies_and_stays_inside_the_budget():
    memory = Conversation(budget_tokens=60, max_turns=4)
    memory.remember("Who painted the Mona Lisa?", "Leonardo da Vinci. He painted it around 1503.")
    memory.remember("Where is it now?", "It hangs in the Louvre in Paris. It is behind glass.")

    assert memory.text() == (
        "User: Who painted the Mona Lisa?\nBeacon: Leonardo da Vinci.\n"
        "User: Where is it now?\nBeacon: It hangs in the Louvre in Paris. It is behind glass."
    )
    for number in range(10):
        memory.remember(f"Question number {number} about something long enough", "An answer. " * 20)
    assert len(memory.exchanges) == 4
    assert (len(memory.text()) + 3) // 4 <= 60
    assert "Question number 9" in memory.text()  # the newest always survives


def test_conversation_is_forgotten_after_silence_or_on_request():
    clock = Clock()
    memory = Conversation(idle_s=300, clock=clock)
    memory.remember("Who wrote Hamlet?", "William Shakespeare.")
    clock.now += 299
    assert memory.last.reply == "William Shakespeare."
    clock.now += 301
    assert memory.exchanges == [] and memory.text() == ""

    memory.remember("Who wrote Hamlet?", "William Shakespeare.")
    memory.forget()
    assert memory.last is None


def test_zero_budget_turns_memory_off():
    memory = Conversation(budget_tokens=0)
    memory.remember("Who wrote Hamlet?", "William Shakespeare.")
    assert memory.exchanges == []


# --- Which words need the conversation ---

@pytest.mark.parametrize("text", [
    "How old is he?", "What about Germany?", "And Spain?", "Why?", "Tell me more", "Where is it?",
    "Reply to her", "Open the second one", "Do the same for Bob", "Who sent it?", "What's their address?",
])
def test_follow_ups_are_recognised(text):
    assert followup.is_follow_up(text)


@pytest.mark.parametrize("text", [
    "What is the capital of France?", "Why is the sky blue?", "What time is it?", "Click Reply",
    "What's on this page?", "Read this to me", "Is it raining?", "Summarize the page here",
])
def test_standalone_requests_skip_memory(text):
    assert not followup.is_follow_up(text)


CONTEXT = "User: Who is the president of France?\nBeacon: Emmanuel Macron is the president of France."


@pytest.mark.parametrize("text, rewritten, accepted", [
    ("How old is he?", "How old is Emmanuel Macron?", True),
    ("Where was he born?", "Where was Emmanuel Macron born?", True),
    ("What about Germany?", "Who is the president of Germany?", True),
    ("How old is he?", "How old is he?", True),  # unchanged is allowed
    ("How old is he?", "Emmanuel Macron is 48 years old.", False),  # answered instead of rewriting
    ("How old is he?", "How old is Joe Biden?", False),  # a name nobody said
    ("Where was he born?", "Who is the president of France?", False),  # dropped the user's words
    ("How old is he?", "", False),
])
def test_a_rewrite_may_only_add_words_from_the_conversation(text, rewritten, accepted):
    result = followup.checked(rewrite(rewritten), text, CONTEXT)
    assert (result == rewritten) if accepted else result is None


def test_a_rewrite_must_use_the_rewrite_tool():
    assert followup.checked(say("How old is Emmanuel Macron?"), "How old is he?", CONTEXT) is None


# --- The loop ---

@pytest.mark.loop
def test_a_follow_up_question_is_rewritten_once_and_answered_without_history(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        model = RecordingModel(
            say("Emmanuel Macron is the president of France."),
            rewrite("How old is Emmanuel Macron?"),
            say("Emmanuel Macron was born in 1977."),
        )
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "How old is he?")

        assert model.tools[1] == ["rewrite"]
        assert "LATEST: How old is he?" in model.last_words[1]
        assert "Emmanuel Macron is the president of France." in model.last_words[1]
        # The answer call sees only the standalone question: no history tokens.
        assert model.messages[2][-1]["content"] == "How old is Emmanuel Macron?"
        assert len(model.messages[2]) == 2
        assert helper.spoken[-1] == "Emmanuel Macron was born in 1977."
        assert helper.snapshot_calls == 0
        records = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
        assert records[-1]["utterance"] == "How old is Emmanuel Macron?"
        assert records[-1]["heard"] == "How old is he?"
        assert brain.conversation.last.said == "How old is Emmanuel Macron?"

    asyncio.run(run())


@pytest.mark.loop
def test_a_standalone_question_costs_no_memory(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        model = RecordingModel(say("Emmanuel Macron."), say("Berlin is the capital of Germany."))
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "What is the capital of Germany?")

        assert len(model.messages) == 2  # no rewrite call
        assert model.messages[1][-1]["content"] == "What is the capital of Germany?"

    asyncio.run(run())


@pytest.mark.loop
@pytest.mark.parametrize("bad_rewrite", [rewrite("How old is Joe Biden?"), BonsaiError("down")])
def test_an_unusable_rewrite_falls_back_to_the_history(tmp_path, bad_rewrite):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        model = RecordingModel(say("Emmanuel Macron is the president of France."), bad_rewrite,
                               say("He was born in 1977."))
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "How old is he?")

        question = model.messages[2][-1]["content"]
        assert question.startswith("EARLIER CONVERSATION:\nUser: Who is the president of France?")
        assert question.endswith("QUESTION: How old is he?")
        assert "Joe Biden" not in question
        assert helper.spoken[-1] == "He was born in 1977."

    asyncio.run(run())


@pytest.mark.loop
def test_an_elliptical_follow_up_is_still_a_question(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        model = RecordingModel(say("Emmanuel Macron is the president of France."), rewrite("And his wife?"),
                               say("Brigitte Macron."))
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "And his wife?")  # no question word: once sent to the action loop

        assert helper.snapshot_calls == 0
        assert model.messages[2][-1]["content"].endswith("QUESTION: And his wife?")
        assert helper.spoken[-1] == "Brigitte Macron."

    asyncio.run(run())


@pytest.mark.loop
def test_a_follow_up_to_a_screen_exchange_reads_the_screen_again(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(
            say("Mail inbox. Sarah Chen asked about lunch tomorrow."),
            rewrite("What time does Sarah Chen want lunch tomorrow?"),
            say("At noon."),
        )
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "What is on my screen?")
        snapshots = helper.snapshot_calls
        await hear(brain, "What time does she want it?")

        assert helper.snapshot_calls > snapshots
        assert "SCREEN:" in model.last_words[2]
        assert "REQUEST: What time does Sarah Chen want lunch tomorrow?" in model.last_words[2]
        assert helper.spoken[-1] == "At noon."

    asyncio.run(run())


@pytest.mark.loop
def test_a_follow_up_action_gets_the_history_in_its_task_prompt(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(
            say("Sarah Chen sent the newest message, about lunch tomorrow."),
            ToolCall("c", "click", {"id": 3}),
            ToolCall("d", "done", {"summary": "I clicked Reply."}),
        )
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who sent the newest message here?")
        await hear(brain, "Reply to her")

        task = model.messages[1]
        assert task[1]["content"].startswith("EARLIER CONVERSATION:\nUser: Who sent the newest message here?")
        assert "Sarah Chen sent the newest message" in task[1]["content"]
        assert task[-1]["content"].endswith("REQUEST: Reply to her")
        assert model.tools[1] != ["rewrite"]  # actions are not rewritten

    asyncio.run(run())


@pytest.mark.loop
def test_a_standalone_action_gets_no_history(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("mail_inbox"))
        model = RecordingModel(say("Emmanuel Macron."), ToolCall("c", "click", {"id": 3}),
                               ToolCall("d", "done", {"summary": "Opened a reply."}))
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "Click Reply")

        assert not any("EARLIER CONVERSATION" in str(m.get("content")) for m in model.messages[1])

    asyncio.run(run())


@pytest.mark.loop
def test_forget_that_clears_the_conversation(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        model = RecordingModel(say("Emmanuel Macron."), say("I'm not sure who you mean."))
        brain = brain_for(helper, model, tmp_path)

        await hear(brain, "Who is the president of France?")
        await hear(brain, "Forget that")
        assert helper.spoken[-1] == "Okay. I've forgotten our conversation."
        await hear(brain, "How old is he?")

        assert len(model.messages) == 2  # no rewrite: nothing to resolve against
        assert model.messages[1][-1]["content"] == "How old is he?"

    asyncio.run(run())
