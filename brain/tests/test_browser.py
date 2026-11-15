"""Simple browser requests run straight away (browser.py); real gaps still ask; how questions are posed."""

import asyncio
import json
from pathlib import Path

import pytest

from beacon_brain import browser, compose, grounding
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain, answer_hint
from test_loop import ReplayHelper, ScriptedModel, imessage_screen

FIXTURES = Path(__file__).resolve().parents[2] / "scripts" / "fixtures"


def chrome():
    return json.loads((FIXTURES / "chrome_article.json").read_text(encoding="utf-8"))


def steps(utterance, app="Google Chrome"):
    plan = browser.plan(utterance, app)
    return None if plan is None else [(call.name, call.arguments) for call in plan.steps]


NEW_TAB = ("press_key", {"keys": "cmd+t"})
ADDRESS_BAR = ("press_key", {"keys": "cmd+l"})
RETURN = ("press_key", {"keys": "return"})


@pytest.mark.parametrize("utterance, expected", [
    ("Open a new tab and search amazon", [NEW_TAB, ("type_text", {"text": "amazon"}), RETURN]),
    ("Open a new tab.", [NEW_TAB]),
    ("new tab", [NEW_TAB]),
    ("Can you open a new tab, then go to github dot com please",
     [NEW_TAB, ("type_text", {"text": "github.com"}), RETURN]),
    ("Open youtube in a new tab", [NEW_TAB, ("type_text", {"text": "youtube.com"}), RETURN]),
    ("Search for cats", [ADDRESS_BAR, ("type_text", {"text": "cats"}), RETURN]),
    ("Google best pizza near me", [ADDRESS_BAR, ("type_text", {"text": "best pizza near me"}), RETURN]),
    ("Go to youtube", [ADDRESS_BAR, ("type_text", {"text": "youtube.com"}), RETURN]),
    ("Go to wikipedia.org", [ADDRESS_BAR, ("type_text", {"text": "wikipedia.org"}), RETURN]),
    ("Open the youtube website", [ADDRESS_BAR, ("type_text", {"text": "youtube.com"}), RETURN]),
])
def test_browser_requests_plan_keyboard_steps(utterance, expected):
    assert steps(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "open notes",  # may be the Notes app
    "go to the next page",
    "search for cats on this page",
    "search for cats then click the first result",
    "open a new tab and read it to me",
    "send a message to Emmett",
    "click search",
    "scroll down",
])
def test_other_requests_are_left_to_the_model(utterance):
    assert steps(utterance) is None


def test_fast_path_only_in_a_browser():
    assert steps("Search for cats", app="Mail") is None
    assert steps("Open a new tab", app="Finder") is None


def run(screen, utterance, *calls, replies=(), **config):
    async def go():
        helper = ReplayHelper(screen)
        huds = []
        original_hud = helper.hud

        async def hud(state, **fields):
            huds.append((state, fields.get("caption")))
            await original_hud(state, **fields)

        helper.hud = hud
        model = ScriptedModel(*calls)
        brain = helper.brain = Brain(helper, model, Config(log_path=Path("/dev/null"), settle_ms=0,
                                                           reply_timeout_s=0.3, **config), "x")
        pending = list(replies)
        original_speak = helper.speak

        async def speak(text, rate):
            speech_id = await original_speak(text, rate)
            if pending and (text.endswith("say your answer.") or text.endswith("Say yes to continue.")):
                reply = pending.pop(0)
                asyncio.get_running_loop().call_later(
                    0.01, lambda: asyncio.ensure_future(brain.on_event({"type": "stt.final", "text": reply})))
            return speech_id

        helper.speak = speak
        await brain.on_event({"type": "stt.final", "text": utterance})
        while True:
            task = brain.active
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
            await asyncio.sleep(0.05)
            if brain.active is task or brain.active.done():
                break
        return helper, model, huds

    return asyncio.run(go())


@pytest.mark.loop
@pytest.mark.parametrize("utterance, typed, first_key, summary", [
    ("Open a new tab and search amazon", "amazon", "cmd+t", "I opened a new tab and searched for amazon."),
    ("Open a new tab", None, "cmd+t", "I opened a new tab."),
    ("Search for cats", "cats", "cmd+l", "I searched for cats."),
    ("Go to youtube", "youtube.com", "cmd+l", "I went to youtube.com."),
])
def test_browser_requests_run_without_asking(utterance, typed, first_key, summary):
    # The model would ask where to search; it must not even be consulted.
    helper, model, huds = run(chrome(), utterance, ToolCall("a", "ask_user", {"question": "Where should I search?"}))

    assert model.requests == []
    assert "asking" not in [state for state, _ in huds]
    keys = [p["keys"] for path, p in helper.actions if path == "/input/key"]
    assert keys[0] == first_key
    texts = [p["text"] for path, p in helper.actions if path == "/input/type"]
    assert texts == ([typed] if typed else [])
    if typed:
        assert keys[-1] == "return"
    assert helper.spoken == [summary]


@pytest.mark.loop
def test_open_new_tab_and_search_is_not_a_click_question_outside_a_browser():
    # Grounding used to turn the model's click into "I don't see new tab and search amazon".
    screen = chrome()
    call = ToolCall("a", "click", {"id": 5})  # Reload: no word of the request in its label
    assert grounding.check(call, screen, "Open a new tab and search amazon", fresh_request=True) == call
    # A single named target is still checked.
    assert grounding.check(call, screen, "Click amazon", fresh_request=True).name == "ask_user"


@pytest.mark.loop
def test_send_without_content_still_asks_what_to_say():
    helper, model, huds = run(imessage_screen(), "Send a message to Emmett",
                              ToolCall("a", "type_text", {"id": 12, "text": "hello"}))

    question = compose.ASK_MESSAGE
    assert helper.spoken[0] == f"{question} {answer_hint('right_option')}"
    assert ("asking", question) in huds
    assert not [a for a in helper.actions if a[0] == "/input/type"]


@pytest.mark.loop
def test_question_uses_asking_state_and_a_hold_key_hint():
    helper, model, huds = run(imessage_screen(), "Send a message to Emmett",
                              ToolCall("a", "done", {"summary": "Done."}), ptt_key="right_command")

    assert helper.spoken[0] == "What should the message say? Hold the Command key and say your answer."
    assert ("asking", "What should the message say?") in huds  # caption carries no hint
    assert "speaking" not in [state for state, _ in huds[:huds.index(("asking", compose.ASK_MESSAGE)) + 1]]


@pytest.mark.loop
def test_confirm_prompt_keeps_confirm_state():
    async def go():
        helper = ReplayHelper(imessage_screen())
        huds = []

        async def hud(state, **fields):
            huds.append((state, fields.get("caption")))

        helper.hud = hud
        brain = helper.brain = Brain(helper, ScriptedModel(), Config(log_path=Path("/dev/null"),
                                                                     reply_timeout_s=0.2), "x")
        asyncio.get_running_loop().call_later(
            0.05, lambda: asyncio.ensure_future(brain.on_event({"type": "stt.final", "text": "yes"})))
        reply = await brain.ask("Send hello to Emmett. Say yes to continue.", confirm=True)
        return helper, huds, reply

    helper, huds, reply = asyncio.run(go())
    assert reply == "yes"
    assert huds[0] == ("confirm", "Send hello to Emmett. Say yes to continue.")
    assert helper.spoken == ["Send hello to Emmett. Say yes to continue."]


def test_answer_hint_names_the_held_key():
    assert answer_hint("right_option") == "Hold the Option key and say your answer."
    assert answer_hint("fn") == "Hold the Fn key and say your answer."
