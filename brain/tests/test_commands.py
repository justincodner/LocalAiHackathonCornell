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


RUNNING = ["Finder", "Google Chrome", "Mail", "Messages", "Notes"]
INSTALLED = ["App Store", "Calculator", "Discord", "FaceTime", "Finder", "Google Chrome", "IntelliJ IDEA",
             "IntelliJ IDEA CE", "Mail", "Messages", "Notes", "Photo Booth", "Photos", "Safari", "System Settings",
             "Terminal", "zoom.us"]


def opened(utterance):
    request = commands.app_request(utterance)
    return None if request is None else commands.match_app(request[0], RUNNING, INSTALLED)


@pytest.mark.parametrize("utterance, app", [
    ("Open messages.", "Messages"), ("open the messages app", "Messages"), ("Launch Discord", "Discord"),
    ("switch to chrome", "Google Chrome"), ("Bring up notes", "Notes"), ("open settings", "System Settings"),
    ("go to safari", "Safari"), ("open zoom", "zoom.us"), ("bring Mail to the front", "Mail"),
    ("Can you open up Finder please", "Finder"), ("open discored", "Discord"), ("open calculator", "Calculator"),
    ("open intellij", "IntelliJ IDEA"), ("start facetime", "FaceTime"), ("open photos", "Photos"),
    ("open my email", "Mail"), ("Can you open messages?", "Messages"), ("could you please launch discord", "Discord"), ("open imessage", "Messages"), ("Open the App Store", "App Store"),
])
def test_open_requests_name_an_installed_app(utterance, app):
    assert opened(utterance) == app


@pytest.mark.parametrize("utterance", [
    "open youtube", "open the reply", "open the first email", "open this", "open it", "open a new tab",
    "open amazon on the new tab", "open the link", "open messages and text sam", "open youtube.com",
    "open the youtube website", "go back", "switch to the next tab", "open word",  # Word isn't installed
])
def test_other_open_requests_are_not_apps(utterance):
    assert opened(utterance) is None


@pytest.mark.loop
def test_open_app_runs_without_a_model_call():
    helper, model = run(chrome(), "Open messages.")
    assert helper.actions == [("/app/open", {"name": "Messages"})]
    assert model.requests == [] and helper.snapshot_calls == 0
    assert helper.spoken == ["Opened Messages."]


@pytest.mark.loop
def test_go_to_a_site_name_in_a_browser_stays_a_website():
    helper, model = run(chrome(), "Go to discord")
    assert ("/app/open", {"name": "Discord"}) not in helper.actions
    assert ("/input/key", {"keys": "cmd+l"}) in helper.actions  # browser.py types discord.com


@pytest.mark.loop
def test_open_terminal_is_left_to_the_guarded_loop():
    helper, model = run(chrome(), "Open terminal", ToolCall("a", "open_app", {"name": "Terminal"}))
    assert ("/app/open", {"name": "Terminal"}) not in helper.actions


@pytest.mark.loop
def test_unknown_app_falls_back_to_the_model():
    helper, model = run(chrome(), "Open youtube", ToolCall("a", "say", {"text": "Which one?"}))
    assert len(model.requests) == 1


@pytest.mark.loop
def test_polite_open_question_opens_the_app():
    helper, model = run(chrome(), "Can you open messages?")
    assert helper.actions == [("/app/open", {"name": "Messages"})] and model.requests == []


@pytest.mark.loop
def test_asking_the_user_how_to_do_the_request_is_a_failed_step():
    helper, model = run(chrome(), "Open the inbox",
                        ToolCall("a", "ask_user", {"question": "How do I access messages in this app?"}),
                        ToolCall("b", "ask_user", {"question": "How do I open the inbox?"}),
                        ToolCall("c", "done", {"summary": "stopped"}))
    assert not any("How do I" in text for text in helper.spoken)


def poker():
    import json
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "scripts" / "fixtures" / "chrome_poker.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("words, label", [
    ("Fold.", "Fold"), ("Fold,", "Fold"), ("fold", "Fold"), ("Call", "Call $4"), ("press call", "Call $4"),
    ("Raise", "Raise to $11"), ("raise to 11", "Raise to $11"), ("I'll fold", "Fold"), ("click the fold button", "Fold"),
])
def test_named_button(words, label):
    assert commands.named_button(words, poker())["label"] == label


@pytest.mark.parametrize("words", ["check", "Fold or raise", "close", "reply", "raise to 20"])
def test_named_button_needs_one_visible_match(words):
    assert commands.named_button(words, poker()) is None


def test_bare_only_takes_a_label_said_on_its_own():
    assert commands.named_button("press call", poker(), bare=True) is None
    assert commands.named_button("Fold.", poker(), bare=True)["label"] == "Fold"


@pytest.mark.loop
def test_one_word_fold_presses_fold_with_no_model_call():
    helper, model = run(poker(), "Fold.")
    assert helper.actions == [("/ax/act", {"snapshot_id": poker()["snapshot_id"], "element_id": 17, "action": "press"})]
    assert model.requests == []


@pytest.mark.loop
def test_an_answer_naming_a_button_overrides_the_models_pick():
    async def go():
        helper = ReplayHelper(poker())
        model = ScriptedModel(ToolCall("a", "ask_user", {"question": "Fold or raise?"}),
                              ToolCall("b", "click", {"id": 19}),  # the model picks Raise
                              ToolCall("c", "done", {"summary": "Done."}))
        brain = helper.brain = Brain(helper, model, Config(log_path=Path("/dev/null"), settle_ms=0,
                                                           reply_timeout_s=2), "Test")
        await brain.on_event({"type": "stt.final", "text": "Make a move in this hand", "stt_ms": 0})
        await helper.clarifying.wait()
        await brain.on_event({"type": "stt.final", "text": "Fold,", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    pressed = [p["element_id"] for path, p in helper.actions if path == "/ax/act" and p.get("action") == "press"]
    assert pressed == [17]


@pytest.mark.parametrize("utterance", [
    "Look at my poker situation. What do I do here? Fold or raise 2.3.", "Do I raise or do I call?",
    "do i raise or do i call", "What should I do here?",
])
def test_choice_questions_get_advice_from_the_screen(utterance):
    from beacon_brain import fastpath
    assert fastpath.wants_advice(utterance) and fastpath.needs_screen(utterance) and fastpath.is_question(utterance)
