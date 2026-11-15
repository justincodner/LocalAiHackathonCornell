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
        self.polls = getattr(self, "polls", 0) + 1
        if getattr(self, "group_first", False) and self.polls < 6:
            return [chat for chat in self.CHATS if words in chat.casefold() and chat.casefold() != words]
        return [chat for chat in self.CHATS if words in chat.casefold()] + (
            ["David", "David & Clav"] if words == "poker" else [])  # messages that mention poker

    async def frontmost(self):
        return {"app": self.app, "window": self.window, "elements": [], "snapshot_id": "s_front"}

    async def apps(self):
        return {"running": ["Messages", "Google Chrome", "Notes"],
                "installed": ["Messages", "Google Chrome", "Notes", "Calculator", "Clock", "Calendar", "Discord"]}

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

    SIDEBAR = []  # chat names listed in Messages' sidebar (empty: only search finds them)

    async def request(self, path, payload=None):
        self.actions.append((path, payload))
        if path == "/ax/find":
            if self.app != "Messages" or self.query is not None:
                return {"ok": True, "matches": []}
            rows = [{"id": 50 + i, "snapshot_id": "s_find", "role": "text", "bounds": [305, 136 + 80 * i, 300, 80],
                     "label": f"{chat}, \u200eHow are you?, 12:59 AM", "states": []}
                    for i, chat in enumerate(self.SIDEBAR)]
            # A sender name in the open chat matches too, but it is a one-line text.
            rows.append({"id": 90, "snapshot_id": "s_find", "role": "text", "bounds": [635, 381, 28, 13],
                         "label": f"{payload['query']}, Oh mb, 1:14 AM", "states": []})
            words = payload["query"].casefold()
            return {"ok": True, "matches": [row for row in rows if words in row["label"].casefold()]}
        if path == "/ax/act" and payload.get("action") == "press" and 50 <= payload["element_id"] < 90:
            self.window = self.SIDEBAR[payload["element_id"] - 50]
        if path == "/ax/act" and payload.get("action") == "focus" and payload["element_id"] == 99:
            self.focus = "message"
        elif path == "/app/open":
            if payload["name"] == "Messages" and self.app != "Messages":
                self.window = "David"  # Messages comes forward on its last chat
            self.app = payload["name"]
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
    assert helper.spoken == ["Sent to Poker: hello."]


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


JUSTIN = "No, I'm asking, I want you to send to this chat, did you find the pirate token?"


def chat_screen(name="Big Kizaru"):
    return {"snapshot_id": "s_c", "app": "Messages", "window": name, "focused_id": 12, "text": "",
            "elements": [{"id": 11, "role": "heading", "label": name, "states": []},
                         {"id": 13, "role": "text", "label": "Private channels", "states": []},
                         {"id": 12, "role": "textfield", "label": "Message", "value": "", "states": ["focused"]}]}


@pytest.mark.parametrize("utterance, message", [
    (JUSTIN, "did you find the pirate token?"),
    ("Can you send, did you guys really find the pirate token?", "did you guys really find the pirate token?"),
    ("send on my way in this chat", "on my way"),
    ("send hello here", "hello"),
    ("post to this chat: who is in tonight?", "who is in tonight?"),
    ("send hello", "hello"),
    ("Send hello to Big Kizaru", "hello"),
])
def test_here_send_types_into_the_open_chat(utterance, message):
    assert chats.here_send(utterance, chat_screen()) == chats.Request("Big Kizaru", "Messages", message, True)


def test_here_send_leaves_other_requests_alone():
    assert chats.here_send("Send hello to Emmett", chat_screen()) is None  # someone else's chat is open
    assert chats.here_send("send a message to this chat", chat_screen()) is None  # no message yet: ask
    assert chats.here_send("what's in this chat", chat_screen()) is None
    assert chats.here_send(JUSTIN, {**chat_screen(), "app": "Mail"}) is None


def test_the_message_is_not_respelled_from_the_screen():
    from beacon_brain import screen_words
    assert "pirate token" in screen_words.correct(JUSTIN, chat_screen())


@pytest.mark.loop
def test_send_to_this_chat_types_and_sends_without_asking():
    async def go():
        helper = MessagesHelper()
        helper.app, helper.window = "Messages", "Big Kizaru"
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": JUSTIN, "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.sent == [("Big Kizaru", "did you find the pirate token?")]
    assert not any("?" in text and "Sent" not in text for text in helper.spoken)


def test_send_a_message_without_text_still_asks():
    assert chats.here_send("Send Emmett a message.", chat_screen("Emmett")) is None
    assert chats.here_send("send a text", chat_screen()) is None


@pytest.mark.parametrize("utterance, name, message", [
    ("Switch to Clav", "Clav", None), ("Please switch the clav", "clav", None),
    ('Can you go to Clav and send, "How are you doing?"', "Clav", "How are you doing?"),
    ("go to Kai and say on my way", "Kai", "on my way"),
])
def test_bare_switch_names_a_chat(utterance, name, message):
    assert chats.bare_switch(utterance) == chats.Request(name, None, message, message is not None)


@pytest.mark.parametrize("utterance", [
    "switch to the next tab", "go to the top", "go back", "Go to discord and send a message to KingSneapeshot.hello",
    "switch to messages", "go to the inbox",
])
def test_bare_switch_leaves_places_and_apps_alone(utterance):
    assert chats.bare_switch(utterance) is None


def test_a_conversation_row_is_spoken_by_its_name():
    from beacon_brain import labels
    assert labels.short("Clav, Damn, 1:18 AM Mostly White People, Sure, 1:18 AM David, How are you doing?") == "Clav"
    assert labels.short("Fold") == "Fold"
    assert chats.is_conversation_list({"label": "David, How are you?, 11:45 PM Kai Kuriyama, alr, 10:46 PM"})


@pytest.mark.loop
def test_switch_to_clav_in_messages_uses_search():
    async def go():
        helper = MessagesHelper()
        helper.CHATS = [*MessagesHelper.CHATS, "Clav"]
        helper.app, helper.window = "Messages", "David"
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": "Switch to Clav", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.window == "Clav" and helper.spoken == ["Opened the Clav chat."]



def selected_list_screen(helper):
    """Messages' real snapshot: the whole chat list is one selected text holding every name."""
    original = helper.snapshot

    async def snapshot():
        screen = await original()
        row = {"id": 3, "role": "text", "states": ["selected"],
               "label": "David, How are you doing?, 11:45 PM Clav, Damn, 1:18 AM"}
        return {**screen, "elements": [row, *screen["elements"]]}

    helper.snapshot = snapshot


@pytest.mark.loop
@pytest.mark.parametrize("group_first", [False, True])
def test_go_to_david_from_the_clav_chat_opens_david_not_a_group(group_first):
    async def go():
        helper = MessagesHelper()
        helper.CHATS = ["David & Clav", "Alvin, David & Clav", "Clav", "David", "Poker"]
        helper.app, helper.window, helper.group_first = "Messages", "Clav", group_first
        selected_list_screen(helper)
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": "Go to David.", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.window == "David"
    assert helper.spoken == ["Opened the David chat."]


def test_exact_name_wins_over_a_group_that_contains_it():
    labels = ["David & Clav", "Alvin, David & Clav", "David"]
    assert chats.exact_index("david", labels) == 2
    assert chats.title_is("David", {"window": "David"}) and not chats.title_is("David", {"window": "David & Clav"})


@pytest.mark.loop
@pytest.mark.parametrize("utterance", ["Switch the David", "Go to David", "switch to David", "Go to the David chat"])
def test_a_chat_in_the_sidebar_is_clicked_not_searched(utterance):
    async def go():
        helper = MessagesHelper()
        helper.SIDEBAR = ["Clav", "David & Clav", "Alvin, David & Clav", "David", "Poker"]
        helper.app, helper.window = "Messages", "Clav"
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": utterance, "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.window == "David" and helper.spoken == ["Opened the David chat."]
    presses = [p for path, p in helper.actions if path == "/ax/act" and p.get("action") == "press"]
    assert [p["element_id"] for p in presses] == [53]  # the "David" row, not "David & Clav"
    assert not any(path in {"/input/type", "/input/key"} for path, _ in helper.actions)


@pytest.mark.loop
def test_a_chat_not_in_the_sidebar_falls_back_to_search():
    async def go():
        helper = MessagesHelper()
        helper.SIDEBAR = ["Clav", "David & Clav"]
        helper.app, helper.window = "Messages", "Clav"
        brain = helper.brain = Brain(helper, None, Config(log_path=Path("/dev/null"), settle_ms=0), "Test")
        await brain.on_event({"type": "stt.final", "text": "Go to Poker", "stt_ms": 0})
        await brain.active
        return helper

    helper = asyncio.run(go())
    assert helper.window == "Poker" and ("/input/key", {"keys": "cmd+f"}) in helper.actions


def test_sidebar_rows_need_the_exact_name():
    rows = [{"id": 1, "role": "text", "bounds": [305, 136, 300, 80], "label": "Alvin, David & Clav, hey, 1:00 AM"},
            {"id": 2, "role": "text", "bounds": [305, 216, 300, 80], "label": "David & Clav, yo, 1:00 AM"},
            {"id": 3, "role": "text", "bounds": [305, 297, 300, 80], "label": "David, \u200eDraft: Hi, 12:59 AM"},
            {"id": 4, "role": "group", "bounds": [295, 381, 1024, 30], "label": "David, Oh mb, 1:14 AM"}]
    assert [row["id"] for row in chats.sidebar_rows("david", rows)] == [3]
    assert [row["id"] for row in chats.sidebar_rows("Alvin", rows)] == [1]  # the title check then rejects it
