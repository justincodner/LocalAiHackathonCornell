"""Send requests in web chat apps: reach the conversation, type the message, press Return.

Reproduces "go to discord and send a message to KingSneapeshot.hello", which clicked the Discord
tab, then "More" on a message, and stopped without typing anything.
"""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from beacon_brain import compose, labels, narrate
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain

FIXTURES = Path(__file__).resolve().parents[2] / "scripts" / "fixtures"
DISCORD_TAB = "(8623) Discord | @Kingsnipeshot - Memory usage - 383 MB"


def screen(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def element(snapshot, label):
    return next(item for item in snapshot["elements"] if item["label"] == label)


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = iter(calls)
        self.requests = 0

    async def next_tool_call(self, messages, tools):
        self.requests += 1
        return next(self.calls)


class ChromeHelper:
    """Serves a screen; pressing the Discord tab switches to the open DM."""

    def __init__(self, first):
        self.screen = first
        self.spoken, self.actions, self.captions = [], [], []
        self.brain = None

    async def snapshot(self):
        return copy.deepcopy(self.screen)

    async def hud(self, state, **fields):
        if "caption" in fields:
            self.captions.append(fields["caption"])

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        return speech_id

    async def request(self, path, payload):
        self.actions.append((path, payload))
        if path == "/ax/act" and payload["action"] == "press":
            pressed = next(item for item in self.screen["elements"] if item["id"] == payload["element_id"])
            if pressed["label"] == DISCORD_TAB and self.screen["snapshot_id"] != "s_discord":
                self.screen = screen("chrome_discord_dm")
        return {"ok": True}


def run(helper, model, utterance, tmp_path):
    async def go():
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", settle_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": utterance})
        await asyncio.wait_for(brain.active, timeout=3)
    asyncio.run(go())


@pytest.mark.parametrize("utterance, name, body", [
    ("Go to discord and send a message to KingSneapeshot.hello.", "KingSneapeshot", "hello"),
    ("go to discord and send a message to KingSneapeshot hello", "KingSneapeshot", "hello"),
    ("go to discord and send a message to KingSneapeshot. hello.", "KingSneapeshot", "hello."),
    ("send a message to kingsnipeshot see you at 5", "kingsnipeshot", "see you at 5"),
    ("Go to Slack and send a message to Sam saying the build is green", "Sam", "the build is green"),
    ("Go to Discord and text Sam I'm late", "Sam", "I'm late"),
    ("Send a message to Sarah Chen.", "Sarah", None),  # a surname, not a message: ask
])
def test_send_message_to_name_then_body(utterance, name, body):
    assert compose.recipient(utterance) == name
    assert compose.command_and_content(utterance)[1] == body


def test_discord_composer_and_open_chat_are_found():
    dm = screen("chrome_discord_dm")
    assert compose.message_field(dm)["label"] == "Message @Kingsnipeshot"
    assert compose.chat_open("KingSneapeshot", dm)  # misheard, still the open DM
    assert not compose.chat_open("Emmett", dm)  # only in the sidebar
    assert compose.shown_name("KingSneapeshot", dm) == "Kingsnipeshot"


def test_tab_noise_is_never_spoken():
    assert labels.clean(DISCORD_TAB, "tab") == "Discord | @Kingsnipeshot"
    assert labels.clean("Inbox – Memory usage – 1.2 GB") == "Inbox"
    assert labels.clean("Memory usage notes - Docs") == "Memory usage notes - Docs"
    other = screen("chrome_discord_other_tab")
    tab = element(other, DISCORD_TAB)
    call = ToolCall("a", "click", {"id": tab["id"]})
    assert narrate.past_action(call, other) == "clicked Discord | @Kingsnipeshot"
    assert narrate.action_text(call, other) == "Clicking Discord | @Kingsnipeshot."
    cleaned = labels.clean_snapshot(copy.deepcopy(other))
    assert "Memory usage" not in cleaned["text"] and "(8623)" not in cleaned["text"]
    assert element(cleaned, "Discord | @Kingsnipeshot")["role"] == "tab"


@pytest.mark.loop
def test_go_to_discord_and_send_types_in_the_composer_instead_of_clicking_more(tmp_path):
    other = screen("chrome_discord_other_tab")
    dm = screen("chrome_discord_dm")
    helper = ChromeHelper(other)
    model = ScriptedModel(
        ToolCall("a", "click", {"id": element(other, DISCORD_TAB)["id"]}),
        ToolCall("b", "click", {"id": element(dm, "More")["id"]}),  # what the 2B model did: never asked now
        ToolCall("c", "done", {"summary": "Done."}),
    )
    run(helper, model, "Go to discord and send a message to KingSneapeshot.hello.", tmp_path)

    composer = element(dm, "Message @Kingsnipeshot")["id"]
    assert helper.actions[1:] == [
        ("/ax/act", {"snapshot_id": "s_discord", "element_id": composer, "action": "focus"}),
        ("/input/type", {"text": "hello"}),
        ("/input/key", {"keys": "return"}),
    ]
    assert model.requests == 0  # the Discord tab is clicked and the message sent with no model call
    assert helper.spoken[-1] == "Sent to Kingsnipeshot: hello."
    assert not any("memory usage" in text.casefold() or "8623" in text for text in helper.spoken + helper.captions)


@pytest.mark.loop
def test_model_typing_into_the_wrong_element_is_retargeted_and_reply_is_not_pressed(tmp_path):
    dm = screen("chrome_discord_dm")
    helper = ChromeHelper(dm)
    model = ScriptedModel(
        ToolCall("a", "type_text", {"id": dm["focused_id"], "text": "hello"}),  # the web area
        ToolCall("b", "click", {"id": element(dm, "Reply")["id"]}),
    )
    run(helper, model, "send a message to Kingsnipeshot hello", tmp_path)

    composer = element(dm, "Message @Kingsnipeshot")["id"]
    assert ("/ax/act", {"snapshot_id": "s_discord", "element_id": composer, "action": "focus"}) in helper.actions
    assert helper.actions[-1] == ("/input/key", {"keys": "return"})
    assert not any(path == "/ax/act" and payload["action"] == "press" for path, payload in helper.actions)


@pytest.mark.loop
def test_done_before_typing_is_not_the_end_of_a_send(tmp_path):
    other = screen("chrome_discord_other_tab")
    dm = screen("chrome_discord_dm")
    helper = ChromeHelper(other)
    model = ScriptedModel(
        ToolCall("a", "click", {"id": element(other, DISCORD_TAB)["id"]}),
        ToolCall("b", "done", {"summary": "I opened Discord."}),
    )
    run(helper, model, "go to discord and send a message to KingSneapeshot hello", tmp_path)

    assert ("/input/type", {"text": "hello"}) in helper.actions
    assert helper.actions[-1] == ("/input/key", {"keys": "return"})


@pytest.mark.loop
def test_clicking_another_dm_in_the_sidebar_is_kept(tmp_path):
    dm = screen("chrome_discord_dm")
    helper = ChromeHelper(dm)
    emmett = element(dm, "Emmett (direct message)")["id"]
    model = ScriptedModel(
        ToolCall("a", "click", {"id": emmett}),
        ToolCall("b", "ask_user", {"question": "What should I say?"}),  # the message is given: rejected
        ToolCall("c", "done", {"summary": "Stopped."}),
    )

    async def go():
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", settle_ms=0,
                                                           reply_timeout_s=0.1), "x")
        await brain.on_event({"type": "stt.final", "text": "send a message to Emmett see you soon"})
        await asyncio.wait_for(brain.active, timeout=3)
    asyncio.run(go())

    assert helper.actions[0] == ("/ax/act", {"snapshot_id": "s_discord", "element_id": emmett, "action": "press"})
    assert not any(path == "/input/type" for path, _ in helper.actions)  # Emmett's chat never opened here


def test_plan_replaces_a_stray_click_only_in_the_named_persons_open_chat():
    dm = screen("chrome_discord_dm")
    more = ToolCall("a", "click", {"id": element(dm, "More")["id"]})
    composer = element(dm, "Message @Kingsnipeshot")["id"]
    planned = compose.plan(more, "send a message to KingSneapeshot hello", [], dm, strict=True)
    assert (planned.name, planned.arguments) == ("type_text", {"id": composer, "text": "hello"})
    assert compose.plan(more, "send a message to Emmett hello", [], dm, strict=True) is None


def test_send_buttons_skip_a_messages_reply_above_the_composer():
    dm = screen("chrome_discord_dm")
    composer = element(dm, "Message @Kingsnipeshot")["id"]
    assert Brain.send_buttons(dm, composer) == []
    done = ToolCall("a", "done", {"summary": ""})
    assert Brain.send_typed(done, "type_text", False, "send a message to Sam hi", dm, composer).arguments == {
        "keys": "return"}


def test_place_tab_finds_the_named_tab():
    other = screen("chrome_discord_other_tab")
    tab = compose.place_tab("go to discord and send a message to Kingsnipeshot hello", other)
    assert tab is not None and "Discord" in tab["label"]
    assert compose.place_tab("send a message to Kingsnipeshot hello", other) is None
    assert compose.place_tab("go to slack and send a message to Sam hi", other) is None
    assert compose.place_tab("go to discord and send hi to Sam", screen("chrome_discord_dm")) is None
