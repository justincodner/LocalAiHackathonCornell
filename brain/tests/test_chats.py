"""Switching to a named chat and sending to it, from any app (chats.py)."""

import asyncio
import json
from pathlib import Path

import pytest

from beacon_brain import chats
from beacon_brain.config import Config
from beacon_brain.loop import Brain

FIXTURES = Path(__file__).resolve().parents[2] / "scripts" / "fixtures"


@pytest.mark.parametrize("utterance, name, app, message, send", [
    ("Switch to the poker chat", "poker", None, None, False),
    ("Change to the Poker chat.", "Poker", None, None, False),
    ("Can you switch to the poker chat please?", "poker", None, None, False),
    ("Hey Beacon, go to the poker group chat", "poker", None, None, False),
    ("switch over to my chat with Emmett", "Emmett", None, None, False),
    ("open the chat with Sarah Chen", "Sarah Chen", None, None, False),
    ("go back to the poker chat", "poker", None, None, False),
    ("switch to poker in Messages", "poker", "Messages", None, False),
    ("switch to the general channel on Discord", "general", "Discord", None, False),
    ("Send hello to the poker chat", "poker", None, "hello", True),
    ("send a message to the poker chat saying Who is in tonight?", "poker", None, "Who is in tonight?", True),
    ("send a message to the poker chat", "poker", None, None, True),
    ("Text the poker chat I am running late", "poker", None, "I am running late", True),
    ("tell the poker group chat that I am here", "poker", None, "I am here", True),
    ("send hello to Emmett on Discord", "Emmett", "Discord", "hello", True),
])
def test_parse_chat_requests(utterance, name, app, message, send):
    assert chats.parse(utterance) == chats.Request(name, app, message, send)


@pytest.mark.parametrize("utterance", [
    "open messages", "switch to chrome", "go to the next chat", "send hello to Emmett", "switch to the next tab",
    "what's in the poker chat", "press enter", "open a new tab with amazon",
])
def test_other_requests_are_not_chat_switches(utterance):
    assert chats.parse(utterance) is None


def test_best_match_prefers_the_named_chat():
    labels = ["David", "David & Clav", "Alvin, David & Clav", "Poker"]
    assert chats.best_match("poker", labels) == 3
    assert chats.best_match("David", labels) == 0
    assert chats.best_match("david and clav", labels) == 1
    assert chats.best_match("pokker", labels) == 3  # misheard
    assert chats.best_match("Soccer", labels) is None


def test_chat_app_follows_the_screen():
    request = chats.Request("poker", None, None, False)
    assert chats.chat_app(request, {"app": "Google Chrome", "window": "Weekend plans - Google Chrome"}) == "Messages"
    assert chats.chat_app(request, {"app": "Google Chrome", "window": "Discord | #general - Google Chrome"}) == "Discord"
    assert chats.chat_app(chats.Request("poker", "Slack", None, False), {"app": "Mail", "window": ""}) == "Slack"


class MessagesHelper:
    """Chrome in front; Messages behaves like the real app's Cmd+F search (see chats.py)."""

    CHATS = ["David", "David & Clav", "Alvin, David & Clav", "Poker", "Kai Kuriyama"]

    def __init__(self, first_return_ignored=False):
        self.app = "Google Chrome"
        self.window = "Weekend plans - Google Docs - Google Chrome"
        self.query = None
        self.highlight = -1
        self.focus = "page"
        self.spoken = []
        self.actions = []
        self.sent = []
        self.typed = ""
        self.brain = None
        self.ignore_return = first_return_ignored

    def matches(self):
        if not self.query:
            return []
        words = self.query.casefold()
        return [chat for chat in self.CHATS if words in chat.casefold()] + (
            ["David", "David & Clav"] if words == "poker" else [])  # messages that mention poker

    async def frontmost(self):
        return {"app": self.app, "window": self.window, "elements": [], "snapshot_id": "s_front"}

    async def snapshot(self):
        elements = [{"id": 1, "role": "textfield", "label": "Search", "value": self.query or "",
                     "states": ["focused"] if self.focus == "search" else []}]
        if self.app == "Messages" and self.query is not None:
            found = list(dict.fromkeys(self.matches()))
            elements.append({"id": 2, "role": "text", "label": "Conversations", "states": []})
            elements += [{"id": 10 + i, "role": "button", "label": label, "states": []} for i, label in enumerate(found)]
        if self.app == "Messages":
            elements.append({"id": 99, "role": "textfield", "label": "Message", "value": "",
                             "states": ["focused"] if self.focus == "message" else []})
        focused = {"search": 1, "message": 99}.get(self.focus)
        return {"snapshot_id": "s_m", "app": self.app, "window": self.window, "focused_id": focused,
                "text": "", "elements": elements}

    async def hud(self, state, **fields):
        pass

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        return speech_id

    async def listen(self, mode):
        pass

    async def request(self, path, payload=None):
        self.actions.append((path, payload))
        if path == "/app/open":
            self.app = payload["name"]
            if self.app == "Messages":
                self.window = "David"
        elif path == "/input/key":
            keys = payload["keys"]
            if keys == "cmd+f":
                self.focus, self.query = "search", self.query or ""
            elif keys == "down":
                self.highlight += 1
            elif keys == "return" and self.focus == "search":
                if self.ignore_return:
                    self.ignore_return = False
                else:
                    found = list(dict.fromkeys(self.matches()))
                    if 0 <= self.highlight < len(found):
                        self.window = found[self.highlight]
            elif keys == "return" and self.focus == "message":
                self.sent.append((self.window, self.typed))
                self.typed = ""
            elif keys == "escape":
                self.query, self.highlight, self.focus = None, -1, "message"
        elif path == "/input/type":
            if self.focus == "search":
                self.query, self.highlight = payload["text"], -1
            else:
                self.typed += payload["text"]
        return {"ok": True}


def run(utterance, **helper_args):
    async def go():
        helper = MessagesHelper(**helper_args)
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": utterance, "stt_ms": 0})
        await brain.active
        return helper

    return asyncio.run(go())


@pytest.mark.loop
def test_switch_to_the_poker_chat_from_chrome():
    helper = run("Switch to the poker chat")
    assert (helper.app, helper.window, helper.sent) == ("Messages", "Poker", [])
    assert helper.spoken == ["Opened the Poker chat."]


@pytest.mark.loop
def test_send_to_the_poker_chat_from_another_window():
    helper = run("Send hello to the poker chat")
    assert helper.sent == [("Poker", "hello")]
    assert helper.spoken == ["Sent hello to Poker."]


@pytest.mark.loop
def test_an_ignored_first_return_is_retried():
    helper = run("send hi guys to the poker chat", first_return_ignored=True)
    assert helper.sent == [("Poker", "hi guys")]


@pytest.mark.loop
def test_an_unknown_chat_sends_nothing():
    helper = run("Send hello to the soccer chat")
    assert helper.sent == [] and helper.window == "David"
    assert helper.spoken == ["I couldn't find a Messages chat called soccer."]


def test_person_send_from_an_app_without_a_chat():
    notes = {"app": "Notes", "window": "Notes", "elements": [], "text": ""}
    assert chats.person_send("Send hello to Emmett", notes) == chats.Request("Emmett", "Messages", "hello", True)
    assert chats.person_send("Text David I'm running late", notes) == chats.Request(
        "David", "Messages", "I'm running late", True)
    assert chats.person_send("Send a message to Emmett", notes) is None  # no message: the loop asks
    assert chats.person_send("Send hello to Emmett", {**notes, "app": "Google Chrome"}) is None
    assert chats.person_send("Send hello to Emmett", {**notes, "app": "Messages"}) is None


@pytest.mark.loop
def test_send_to_a_person_from_another_app_goes_through_messages():
    async def go():
        helper = MessagesHelper()
        helper.app, helper.window = "Notes", "Notes"
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": "Send hello to Kai", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.sent == [("Kai Kuriyama", "hello")]


class DiscordTabHelper(MessagesHelper):
    """Chrome on a Docs tab with a Discord tab beside it; Cmd+K in Discord opens the typed DM."""

    def __init__(self):
        super().__init__()
        self.screen = json.loads((FIXTURES / "chrome_discord_other_tab.json").read_text(encoding="utf-8"))
        self.dm = json.loads((FIXTURES / "chrome_discord_dm.json").read_text(encoding="utf-8"))
        self.on_discord = False
        self.switcher = None

    async def frontmost(self):
        screen = self.dm if self.on_discord else self.screen
        return {**screen, "elements": []}

    async def snapshot(self):
        if not self.on_discord:
            return self.screen
        name = self.switcher_opened or "Kingsnipeshot"
        window = f"Discord | @{name} - Google Chrome"
        return {**self.dm, "window": window}

    switcher_opened = None

    async def request(self, path, payload=None):
        self.actions.append((path, payload))
        if path == "/ax/act" and payload.get("action") == "press" and payload["element_id"] == 15:
            self.on_discord = True
        elif path == "/input/key" and payload["keys"] == "cmd+k":
            self.switcher = ""
        elif path == "/input/type" and self.switcher is not None:
            self.switcher += payload["text"]
        elif path == "/input/key" and payload["keys"] == "return" and self.switcher is not None:
            self.switcher_opened, self.switcher = self.switcher, None
        elif path == "/input/key" and payload["keys"] == "return":
            self.sent.append((self.switcher_opened, self.typed))
        elif path == "/input/type":
            self.typed += payload["text"]
        return {"ok": True}


@pytest.mark.loop
def test_discord_dm_through_its_browser_tab():
    async def go():
        helper = DiscordTabHelper()
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": "Send see you at 5 to Emmett on Discord", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.on_discord and helper.switcher_opened == "Emmett"
    assert helper.sent == [("Emmett", "see you at 5")]
