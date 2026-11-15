"""Misheard names ("braydin", "emmit") open the listed chat they sound like, and only a listed one."""

import asyncio
from pathlib import Path

import pytest

from beacon_brain import chats, names
from beacon_brain.config import Config
from beacon_brain.loop import Brain
from tests.test_chats import MessagesHelper

LISTED = ["Brayden", "Emmett", "David", "David & Clav", "Alvin, David & Clav", "Poker", "Kai Kuriyama", "Clav"]


@pytest.mark.parametrize("spoken, meant", [
    ("braydin", "Brayden"), ("Braydon", "Brayden"), ("emmit", "Emmett"), ("Emit", "Emmett"), ("emmet", "Emmett"),
    ("clab", "Clav"), ("Kay", "Kai Kuriyama"), ("pokr", "Poker"),
    ("David", "David"), ("poker", "Poker"),  # already right: kept
])
def test_a_misheard_name_matches_the_listed_chat(spoken, meant):
    assert names.resolve(spoken, LISTED) == [meant]


@pytest.mark.parametrize("spoken", ["Sam", "Ella", "Jordan", "Mike", "Dave", "Alvin", "Clav David"])
def test_a_name_nothing_listed_sounds_like_is_left_alone(spoken):
    assert names.resolve(spoken, LISTED) == []


def test_two_names_equally_close_are_a_tie():
    assert names.resolve("braydin", ["Brayden", "Braden"]) == ["Brayden", "Braden"]
    assert names.resolve("brayden", ["Brayden", "Braden"]) == ["Brayden"]
    assert names.resolve("emmit", ["Emmett", "Emma"]) == ["Emmett"]  # not close enough to tie


def test_names_sound_alike():
    assert names.sound_key("Brayden") == names.sound_key("braydin") == "brdn"
    assert names.sound_key("Emmett") == names.sound_key("emmit") == "amt"
    assert names.sound_key("Philip") == names.sound_key("Filip")


def test_sidebar_names_are_read_from_the_chat_list():
    text = ("Brayden, see you at 8, 11:45 PM Emmett, lol, okay, 10:46 PM Alvin, David & Clav, hey, Yesterday "
            "Kai Kuriyama, alr, Monday Poker, Sure, 11/12/26")
    assert chats.listed_names(text) == ["Brayden", "Emmett", "Alvin, David & Clav", "Kai Kuriyama", "Poker"]
    assert chats.row_name("‎David & Clav, ‎yo, 1:00 AM") == "David & Clav"


class Sidebar(MessagesHelper):
    """Messages with the chats listed on the left: the window read shows them as one text."""

    answer = None

    async def snapshot(self):
        screen = await super().snapshot()
        if self.app != "Messages":
            return screen
        row = {"id": 3, "role": "text", "states": ["selected"],
               "label": " ".join(f"{chat}, How are you?, 12:59 AM" for chat in self.SIDEBAR)}
        return {**screen, "elements": [row, *screen["elements"]]}

    async def speak(self, text, rate):
        speech_id = await super().speak(text, rate)
        if text.startswith("Did you mean") and self.answer:
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.brain.on_event({"type": "stt.final", "text": self.answer})))
        return speech_id


def run(utterance, sidebar, answer=None, window="Clav"):
    async def go():
        helper = Sidebar()
        helper.SIDEBAR = sidebar
        helper.CHATS = [*MessagesHelper.CHATS, "Brayden", "Braden", "Emmett", "Emmett Rhodes"]
        helper.app, helper.window, helper.answer = "Messages", window, answer
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": utterance, "stt_ms": 0})
        await brain.active
        return helper

    return asyncio.run(go())


@pytest.mark.loop
@pytest.mark.parametrize("utterance, chat", [
    ("Switch to braydin", "Brayden"), ("go to the emmit chat", "Emmett"), ("switch to emit", "Emmett"),
])
def test_switch_to_a_misheard_name_opens_the_listed_chat(utterance, chat):
    helper = run(utterance, ["Clav", "Brayden", "Emmett", "David"])
    assert helper.window == chat and helper.spoken == [f"Opened the {chat} chat."]
    assert not any(path == "/input/type" for path, _ in helper.actions)  # pressed in the sidebar, not searched


@pytest.mark.loop
def test_tell_a_misheard_name_sends_to_the_listed_chat():
    helper = run("tell emmit I'm outside", ["Clav", "Brayden", "Emmett"])
    assert helper.sent == [("Emmett", "I'm outside")]
    assert helper.spoken == ["Sent to Emmett: I'm outside."]


@pytest.mark.loop
def test_a_misheard_name_never_opens_an_unlisted_contact():
    # Emmett Rhodes is a contact search would find, but only Emmett is listed on the left.
    helper = run("switch to emmit", ["Clav", "Emmett"])
    assert helper.window == "Emmett"


@pytest.mark.loop
def test_a_tie_asks_which_one():
    helper = run("switch to braydin", ["Clav", "Brayden", "Braden"], answer="Braden")
    assert helper.spoken[0].startswith("Did you mean Brayden or Braden?")
    assert helper.window == "Braden" and helper.spoken[-1] == "Opened the Braden chat."


@pytest.mark.loop
def test_a_name_not_listed_still_searches_as_before():
    helper = run("Go to Poker", ["Clav", "Brayden"])
    assert helper.window == "Poker" and ("/input/key", {"keys": "cmd+f"}) in helper.actions
