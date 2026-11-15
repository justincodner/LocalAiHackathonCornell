"""One-step commands run in code with no model call and no window read (commands.py)."""

import asyncio
from pathlib import Path

import pytest

from beacon_brain import commands
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain
from test_loop import ReplayHelper, ScriptedModel, fixture


def steps(utterance, app="Google Chrome"):
    plan = commands.plan(utterance, app)
    return None if plan is None else [(call.name, call.arguments) for call in plan.steps]


def key(keys):
    return ("press_key", {"keys": keys})


def scroll(direction):
    return ("scroll", {"direction": direction})


@pytest.mark.parametrize("utterance, expected", [
    ("Press enter.", [key("return")]),
    ("press return", [key("return")]),
    ("Hit enter", [key("return")]),
    ("Enter", [key("return")]),
    ("Can you press the enter key please", [key("return")]),
    ("Press escape", [key("escape")]),
    ("press tab", [key("tab")]),
    ("Press tab 3 times", [key("tab")] * 3),
    ("Press enter twice", [key("return")] * 2),
    ("press space", [key("space")]),
    ("Press the down arrow", [key("down")]),
    ("press page down", [key("pagedown")]),
    ("Press command shift T", [key("cmd+shift+t")]),
    ("press cmd+c", [key("cmd+c")]),
    ("Press control tab", [key("ctrl+tab")]),
    ("Next tab", [key("ctrl+tab")]),
    ("Switch tabs.", [key("ctrl+tab")]),
    ("switch tab", [key("ctrl+tab")]),
    ("Go to the next tab", [key("ctrl+tab")]),
    ("Previous tab", [key("ctrl+shift+tab")]),
    ("go back a tab", [key("ctrl+shift+tab")]),
    ("Go to tab 3", [key("cmd+3")]),
    ("first tab", [key("cmd+1")]),
    ("Go to the last tab", [key("cmd+9")]),
    ("Reopen the closed tab", [key("cmd+shift+t")]),
    ("Go back.", [key("cmd+[")]),
    ("Back", [key("cmd+[")]),
    ("go back twice", [key("cmd+[")] * 2),
    ("Go forward", [key("cmd+]")]),
    ("Refresh the page", [key("cmd+r")]),
    ("reload", [key("cmd+r")]),
    ("Scroll down.", [scroll("down")]),
    ("Scroll up a bit", [scroll("up")]),
    ("scroll the page down", [scroll("down")]),
    ("Scroll down 3 times", [scroll("down")] * 3),
    ("page down", [scroll("down")]),
    ("Scroll to the top", [key("cmd+up")]),
    ("go to the bottom of the page", [key("cmd+down")]),
    ("Undo", [key("cmd+z")]),
    ("undo that twice", [key("cmd+z")] * 2),
    ("Copy that", [key("cmd+c")]),
    ("Paste", [key("cmd+v")]),
    ("select all", [key("cmd+a")]),
    ("zoom in", [key("cmd+=")]),
])
def test_commands_plan_one_step(utterance, expected):
    assert steps(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "Press call",  # a button on the screen
    "press the pay button",
    "click enter",
    "press enter and go to the next hand",
    "type hello",
    "scroll down to the comments",
    "go back to the inbox",
    "open a new tab",  # browser.py's job
    "what's on my screen",
    "switch to Safari",
    "copy the link",
    "press enter 50 times please and then reply",
])
def test_anything_else_is_left_to_the_model(utterance):
    assert steps(utterance) is None


def test_app_specific_shortcuts_need_the_right_app():
    assert steps("Go back", app="Mail") is None  # cmd+[ is not "back" in Mail
    assert steps("Go to tab 2", app="Notes") is None
    assert steps("Refresh", app="Messages") is None
    assert steps("Go back", app="Finder") == [key("cmd+[")]
    assert steps("Next tab", app="Finder") == [key("ctrl+tab")]
    assert steps("Press enter", app="Messages") == [key("return")]


def test_repeats_are_capped():
    assert steps("press tab 50 times") == [key("tab")] * commands.MAX_REPEAT


def run(screen, utterance, *calls):
    async def go():
        helper = ReplayHelper(screen)
        model = ScriptedModel(*calls)
        brain = helper.brain = Brain(helper, model, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": utterance, "stt_ms": 0})
        await brain.active
        return helper, model

    return asyncio.run(go())


def chrome():
    screen = fixture("notes_list")
    return {**screen, "app": "Google Chrome"}


@pytest.mark.loop
def test_command_runs_without_a_model_call_or_window_read():
    helper, model = run(chrome(), "Press enter.")
    assert helper.actions == [("/input/key", {"keys": "return"})]
    assert model.requests == [] and helper.snapshot_calls == 0
    assert helper.spoken == ["Pressed Enter."]


@pytest.mark.loop
def test_next_tab_and_scroll_say_what_they_did():
    helper, _ = run(chrome(), "Switch tabs")
    assert helper.actions == [("/input/key", {"keys": "ctrl+tab"})]
    assert helper.spoken == ["Switched to the next tab."]
    helper, _ = run(chrome(), "Scroll down twice")
    assert helper.actions == [("/input/scroll", {"direction": "down", "amount": "page"})] * 2
    assert helper.spoken == ["Scrolled down twice."]


@pytest.mark.loop
def test_app_mismatch_falls_back_to_the_model():
    mail = fixture("mail_inbox")
    helper, model = run(mail, "Go back", ToolCall("a", "say", {"text": "Back to what?"}))
    assert len(model.requests) == 1 and helper.snapshot_calls >= 1


@pytest.mark.loop
def test_restricted_app_is_not_fast_pathed():
    terminal = {**fixture("notes_list"), "app": "Terminal"}
    helper, model = run(terminal, "Press enter", ToolCall("a", "press_key", {"keys": "return"}))
    assert ("/input/key", {"keys": "return"}) not in helper.actions  # risk.check refuses in Terminal
