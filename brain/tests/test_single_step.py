"""Requests found by scripts/loop_scenarios.py: repeats, one-step requests, keys vs buttons, page chrome."""

import asyncio
import json
from pathlib import Path

import pytest

from beacon_brain import compose, fastpath, grounding, page_summary, tools
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain
from test_loop import ReplayHelper, ScriptedModel, imessage_screen, poker_screen

FIXTURES = Path(__file__).resolve().parents[2] / "scripts" / "fixtures"


def run(screen, utterance, *calls, replies=()):
    async def go():
        helper = ReplayHelper(screen)
        model = ScriptedModel(*calls)
        brain = helper.brain = Brain(helper, model, Config(log_path=Path("/dev/null"), settle_ms=0,
                                                           reply_timeout_s=1), "x")
        pending = list(replies)

        original = helper.speak

        async def speak(text, rate):
            speech_id = await original(text, rate)
            if text.endswith("Hold the talk key to answer.") and pending:
                reply = pending.pop(0)
                asyncio.get_running_loop().call_later(
                    0.01, lambda: asyncio.ensure_future(brain.on_event({"type": "stt.final", "text": reply})))
            return speech_id

        helper.speak = speak
        await brain.on_event({"type": "stt.final", "text": utterance})
        await asyncio.wait_for(brain.active, timeout=5)
        return helper, model

    return asyncio.run(go())


def presses(helper):
    return [p["element_id"] for path, p in helper.actions if path == "/ax/act" and p.get("action") == "press"]


@pytest.mark.loop
def test_repeated_scroll_finishes_instead_of_scrolling_again():
    helper, model = run(poker_screen(), "Scroll down to the bottom",
                        ToolCall("a", "scroll", {"direction": "down"}),
                        ToolCall("b", "scroll", {"direction": "down"}),
                        ToolCall("c", "scroll", {"direction": "down"}))
    assert [path for path, _ in helper.actions] == ["/input/scroll"]
    assert helper.spoken[-1] == "I scrolled down."


@pytest.mark.loop
def test_scroll_again_is_allowed_when_asked_twice():
    helper, _ = run(poker_screen(), "Scroll down twice",
                    ToolCall("a", "scroll", {"direction": "down"}),
                    ToolCall("b", "scroll", {"direction": "down"}),
                    ToolCall("c", "done", {"summary": "Scrolled twice."}))
    assert [path for path, _ in helper.actions] == ["/input/scroll", "/input/scroll"]


@pytest.mark.loop
def test_repeated_typing_finishes_with_what_was_typed():
    screen = imessage_screen()
    helper, _ = run(screen, "Put 607 555 0100 in the message box",
                    ToolCall("a", "type_text", {"id": 12, "text": "607 555 0100"}),
                    ToolCall("b", "type_text", {"id": 12, "text": "607 555 0100"}))
    assert [path for path, _ in helper.actions].count("/input/type") == 1
    assert helper.spoken[-1] == "I typed: 607 555 0100."


@pytest.mark.loop
def test_single_click_ends_without_another_model_call():
    helper, model = run(poker_screen(), "Press call", ToolCall("a", "click", {"id": 5}))
    assert presses(helper) == [5]
    assert helper.spoken[-1] == "I pressed Call."
    assert len(model.requests) == 1


@pytest.mark.loop
def test_press_named_button_beats_a_made_up_shortcut():
    helper, _ = run(poker_screen(), "Press call.", ToolCall("a", "press_key", {"keys": "cmd+shift+c"}))
    assert presses(helper) == [5]
    assert not any(path == "/input/key" for path, _ in helper.actions)


@pytest.mark.parametrize("utterance", ["Press enter", "Press command c", "Press tab"])
def test_real_keys_stay_keys(utterance):
    call = ToolCall("a", "press_key", {"keys": "return"})
    assert grounding.check(call, poker_screen(), utterance, fresh_request=True) == call


@pytest.mark.loop
def test_best_move_decides_and_presses():
    helper, model = run(poker_screen(), "Make the best move for me.", ToolCall("a", "click", {"id": 6}))
    assert presses(helper) == [6]
    assert model.tools[0] == ["click", "set_value"]
    assert helper.spoken[-1] == "I pressed Raise."


@pytest.mark.loop
def test_answered_choice_clicks_once():
    helper, _ = run(poker_screen(), "Press one of the buttons.",
                    ToolCall("a", "click", {"id": 4}),  # grounding asks which button
                    ToolCall("b", "click", {"id": 4}),
                    ToolCall("c", "click", {"id": 4}),
                    replies=["Fold"])
    assert presses(helper) == [4]


def test_send_without_text_asks_instead_of_inventing_hello():
    call = ToolCall("a", "type_text", {"id": 12, "text": "hello"})
    planned = compose.plan(call, "Send Emmett a message.", [], imessage_screen())
    assert planned is not None and planned.name == "ask_user"
    found = ToolCall("a", "type_text", {"id": 12, "text": "I'm running late"})
    assert compose.plan(found, "Message Emmett, I'm running late", [], imessage_screen()) is None


def test_reading_an_empty_field_is_rejected():
    screen = json.loads((Path(__file__).resolve().parents[2] / "mock_helper" / "fixtures" / "mail_inbox.json")
                        .read_text(encoding="utf-8"))
    assert "empty field" in tools.validate(ToolCall("a", "read_element", {"id": 14}), screen)
    assert tools.validate(ToolCall("a", "read_element", {"id": 15}), screen) is None


def test_page_content_leaves_out_the_browser_tab_strip():
    screen = json.loads((FIXTURES / "chrome_poker.json").read_text(encoding="utf-8"))
    content = page_summary.visible_content(screen)
    assert "Raise to $11" in content and "Board: 7♦ 2♣ 9♠" in content
    assert not {"New Tab", "Close", "Tab search", "Reload"} & set(content)


@pytest.mark.parametrize("text,single", [
    ("Press call", True), ("click the Reply button", True), ("Click reply and type thanks", False),
    ("press tab twice", False), ("Click File, then New", False), ("Send hello", False),
])
def test_single_click(text, single):
    assert fastpath.single_click(text) is single
