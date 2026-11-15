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
    ("Open a new tab and search amazon", [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ("Search for Christmas Gifts, in the Amazon search bar.",
     [ADDRESS_BAR, ("type_text", {"text": "amazon.com/s?k=christmas+gifts"}), RETURN]),
    ("search amazon for headphones", [ADDRESS_BAR, ("type_text", {"text": "amazon.com/s?k=headphones"}), RETURN]),
    ("look up lofi music on youtube",
     [ADDRESS_BAR, ("type_text", {"text": "youtube.com/results?search_query=lofi+music"}), RETURN]),
    ("Search, Amazon,", [ADDRESS_BAR, ("type_text", {"text": "amazon.com"}), RETURN]),
    ("Open a new tab and search for Amazon.com", [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
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
    ('Please open Amazon on the new tab.', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('open amazon on a new tab', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('Open a new tab with amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('open a new tab to amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('new tab amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('open a new tab for amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('In a new tab, open amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('open a tab with amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('open a new tab with amazon on it', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('Open a new tab with the Amazon website', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ('Create a new tab and open amazon', [NEW_TAB, ("type_text", {"text": "amazon.com"}), RETURN]),
    ("new tab, search for cats", [NEW_TAB, ("type_text", {"text": "cats"}), RETURN]),
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
    "open a new tab and read it to me",
    "open a new tab and wait",
    "open a new tab with my email",
    "open the new tab settings",
    "open tabs",
])
def test_other_requests_are_left_to_the_model(utterance):
    assert steps(utterance) is None


def test_fast_path_only_in_a_browser():
    assert steps("Search for cats", app="Mail") is None
    assert steps("Open a new tab", app="Finder") is None


def run(screen, utterance, *calls, replies=(), helper_factory=ReplayHelper, **config):
    async def go():
        helper = helper_factory(screen)
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


def google_results():
    return {**chrome(), "window": "james webb - Google Search", "elements": [
        {"id": 7, "role": "textfield", "label": "Address and search bar", "value": "google.com/search?q=james+webb"},
        {"id": 46, "role": "link", "label": "James E. Webb - Wikipedia", "states": []},
    ]}


def result(element_id, label, **fields):
    return {"id": element_id, "snapshot_id": "s_find", "role": "link", "label": label, "states": [], **fields}


class SearchResultsHelper(ReplayHelper):
    def __init__(self, screen, matches, current_window=None):
        super().__init__(screen)
        self.matches = matches
        self.current_window = current_window

    async def request(self, path, payload):
        response = await super().request(path, payload)
        return {**response, "matches": self.matches} if path == "/ax/find" else response

    async def frontmost(self):
        current = await super().frontmost()
        return {**current, "window": self.current_window} if self.current_window else current


@pytest.mark.loop
@pytest.mark.parametrize("utterance", ["Click on the Wikipedia article.", "Click Wikipedia", "Open the Wikipedia result"])
def test_click_named_google_result_offscreen(utterance):
    matches = [
        result(1, "James E. Webb - Wikipedia"),  # AI Overview
        result(5, "Wikipedia"),  # knowledge panel
        result(7, "James E. Webb Wikipedia https://en.wikipedia.org › wiki › James_E"),
        result(10, "James Webb Space Telescope Wikipedia ...", bounds=[177, 840, 341, 0],
               full_label="James Webb Space Telescope Wikipedia https://en.wikipedia.org › wiki › James_Webb"),
    ]
    helper, model, _ = run(google_results(), utterance, helper_factory=lambda screen: SearchResultsHelper(screen, matches))

    assert model.requests == []
    assert helper.actions == [
        ("/ax/find", {"query": "wikipedia", "limit": 50}),
        ("/ax/act", {"snapshot_id": "s_find", "element_id": 10, "action": "press"}),
    ]
    assert helper.spoken == ["I opened James Webb Space Telescope Wikipedia."]


@pytest.mark.loop
@pytest.mark.parametrize("matches, current_window, spoken", [
    ([result(1, "James E. Webb - Wikipedia")], None, "I couldn't find a web result"),
    ([result(1, "James Webb Wikipedia https://en.wikipedia.org › wiki › James_Webb"),
      result(2, "James Webb Telescope Wikipedia https://en.wikipedia.org › wiki › JWST")], None, "I found more than one"),
    ([result(1, "James Webb Wikipedia https://en.wikipedia.org › wiki › James_Webb")],
     "Another tab", "The active page changed"),
])
def test_explicit_result_requires_one_match_on_the_current_page(matches, current_window, spoken):
    helper, model, _ = run(google_results(), "Click the Wikipedia result",
                           helper_factory=lambda screen: SearchResultsHelper(screen, matches, current_window))

    assert model.requests == []
    assert [path for path, _ in helper.actions] == ["/ax/find"]
    assert helper.spoken[0].startswith(spoken)


def test_result_lookup_leaves_other_pages_and_non_result_commands_alone():
    assert browser.search_result_request("Click the Wikipedia article", chrome()) is None
    assert browser.search_result_request("Go to wikipedia.org", google_results()) is None
    assert browser.search_result_request("Click the first result", google_results()) is None


@pytest.mark.loop
@pytest.mark.parametrize("utterance, typed, first_key, summary", [
    ("Open a new tab and search amazon", "amazon.com", "cmd+t", "I opened a new tab and went to amazon.com."),
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
