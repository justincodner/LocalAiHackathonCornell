"""Questions are answered, never put back to the user as Beacon's own question."""
import asyncio

import pytest

from beacon_brain import fastpath, questions
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain

from test_loop import ReplayHelper, ScriptedModel, fixture

GTO = "Based on GTO Theory, do I fold or raise to 2.3, over RNG of 34?"
ECHO = "Based on GTO theory, should I fold or raise the hand with 2.3 points against an RNG of 34?"
TABLE = [{"id": i, "role": "button", "depth": 0, "label": label}
         for i, label in enumerate(("Fold", "Call 1", "Raise to 2.3"), 1)] + [
    {"id": 9, "role": "text", "depth": 0, "label": "RNG 34"},
]


@pytest.mark.parametrize("text", [
    GTO,
    "According to the chart, should I call or fold?",
    "In your opinion, is this a good price?",
    "Do I fold or raise here?",
    "Fold or raise with RNG 34?",
])
def test_framed_and_choice_questions_route_as_questions(text):
    assert fastpath.is_question(text)
    assert not fastpath.asks_for_action(text)


@pytest.mark.parametrize("text", ["Open a new tab?", "Click fold?", "Raise.", "open a new tab and search amazon"])
def test_commands_stay_commands(text):
    assert not fastpath.is_question(text)


def test_choice_question_is_advice_about_the_screen():
    assert fastpath.wants_advice(GTO)
    assert fastpath.needs_screen(GTO)


def test_echo_detection():
    assert questions.echoes_request(ECHO, GTO)
    assert questions.echoes_request("Should I fold or raise?", "Should I fold or raise here?")
    assert not questions.echoes_request("What should the message say?", "Send a message to Emmett")
    assert not questions.echoes_request("Which Sarah do you mean, Sarah Chen or Sarah Lee?", "Send hi to Sarah")
    assert not questions.echoes_request("Which file?", "Open it")


@pytest.mark.loop
def test_gto_question_is_answered_not_asked_back(tmp_path):
    async def run():
        helper = ReplayHelper(dict(fixture("safari_form"), app="Google Chrome", window="Practice table", elements=TABLE))
        answer = "Raise to 2.3. With an RNG of 34 the mixed strategy favours the raise."
        model = ScriptedModel(ToolCall("a", "say", {"text": answer}))
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
        await brain.on_event({"type": "stt.final", "text": GTO})
        await asyncio.wait_for(brain.active, timeout=2)
        assert "asking" not in helper.hud_states
        assert helper.spoken[-1] == answer
        assert not helper.actions

    asyncio.run(run())


@pytest.mark.loop
def test_echoed_ask_user_on_action_route_becomes_an_answer(tmp_path):
    async def run():
        helper = ReplayHelper(dict(fixture("safari_form"), app="Google Chrome", window="Practice table", elements=TABLE))
        request = "Work out the GTO play between fold and raise to 2.3 with RNG 34"
        assert not fastpath.is_question(request)
        answer = "Raise to 2.3."
        model = ScriptedModel(
            ToolCall("ask", "ask_user", {"question": "What is the GTO play between fold and raise to 2.3 with RNG 34?"}),
            ToolCall("a", "say", {"text": answer}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
        await brain.on_event({"type": "stt.final", "text": request})
        await asyncio.wait_for(brain.active, timeout=2)
        assert "asking" not in helper.hud_states
        assert answer in helper.spoken

    asyncio.run(run())


@pytest.mark.loop
def test_conversation_reset_event_forgets_and_goes_idle(tmp_path):
    async def run():
        helper = ReplayHelper(fixture("safari_form"))
        brain = helper.brain = Brain(helper, ScriptedModel(), Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
        brain.remember("What is the total?", "The total is $118.", "Safari")
        assert brain.conversation.last is not None
        await brain.on_event({"type": "conversation.reset", "reason": "selection_removed"})
        assert brain.conversation.last is None
        assert helper.hud_states[-1] == "idle"

    asyncio.run(run())
