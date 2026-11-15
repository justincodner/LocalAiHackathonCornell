"""Chat sends read in code (messaging.py): who and what, however the user phrases it.

Reproduces "I'm going to the hackathon in the morning" with a Messages chat open, which reached
the model, got "Where should I go to the hackathon?" twice, and ended with "I couldn't find a
valid action for that request."
"""

import asyncio
import copy
from pathlib import Path

import pytest

from beacon_brain import messaging
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain


@pytest.mark.parametrize("utterance, recipient, body", [
    ("send this: on my way", None, "on my way"),
    ("Send the following, see you at 8", None, "see you at 8"),
    ("send the following message to David: see you", "David", "see you"),
    ("tell him I'm running late", None, "I'm running late"),
    ("Tell her that the game starts at 8", None, "the game starts at 8"),
    ("Tell David I'm running late", "David", "I'm running late"),
    ("tell David that I'm here", "David", "I'm here"),
    ("reply sounds good", None, "sounds good"),
    ("Reply with ok", None, "ok"),
    ("respond saying I'm in", None, "I'm in"),
    ("say happy birthday", None, "happy birthday"),
    ("Say, I'll be there in five", None, "I'll be there in five"),
    ("text Clav I'm outside", "Clav", "I'm outside"),
    ("let David know I'll be there", "David", "I'll be there"),
    ("Let him know that I'm on my way", None, "I'm on my way"),
    ("send see you soon to David", "David", "see you soon"),
    ("Send hello to Sarah Chen", "Sarah Chen", "hello"),
    ("Send a message to David saying I'm late", "David", "I'm late"),
    ("send a message to Emmett see you soon", "Emmett", "see you soon"),
    ("Send to Clav, I'm going to the hackathon in the morning", "Clav", "I'm going to the hackathon in the morning"),
    ("Hey Beacon, can you tell David I'm on my way", "David", "I'm on my way"),
    ("tell David to call me", "David", "Call me"),
    ("text him back saying ok", None, "ok"),
    ("Reply to David sounds good", "David", "sounds good"),
    ("send David a text saying hi", "David", "hi"),
    ("Shoot Clav a message that I'm here", "Clav", "I'm here"),
    ("send hello", None, "hello"),
    ("post here: who's in?", None, "who's in?"),
    ("Message David hey what's up", "David", "hey what's up"),
    ("Send, did you find it?", None, "did you find it?"),
    ("say hi to David", "David", "hi"),
    ("Tell everyone I'll be late", None, "I'll be late"),
    ("dm Sam hello", "Sam", "hello"),
    ('Send "see you at 8" to David', "David", "see you at 8"),
    ('Tell David "see you at 8"', "David", "see you at 8"),
    ("Please text Clav I'm running ten minutes late, thanks", "Clav", "I'm running ten minutes late"),
    ("Send David a message. Oh, that sucks.", "David", "Oh, that sucks."),
    ("Send the collab, I'm going to hack it on in the morning.", "collab", "I'm going to hack it on in the morning."),
])
def test_who_and_what(utterance, recipient, body):
    intent = messaging.parse(utterance)
    assert intent is not None and not intent.bare
    assert (intent.recipient, intent.body) == (recipient, body)
    assert messaging.explicit(utterance)


@pytest.mark.parametrize("utterance, kind", [
    ("I'm going to the hackathon in the morning", "send"),
    ("Sounds good, see you then", "send"),
    ("lol that's hilarious", "send"),
    ("I'll be there in ten minutes", "send"),
    ("on my way now", "send"),
    ("Are you coming tonight?", "ask"),
    ("Want to grab lunch tomorrow?", "ask"),
    ("Pizza or tacos for dinner", "ask"),
    ("I think this page is broken", "skip"),  # about the screen
])
def test_bare_sentences(utterance, kind):
    intent = messaging.parse(utterance)
    assert intent is not None and intent.bare
    assert messaging.bare_kind(intent) == kind


@pytest.mark.parametrize("utterance", [
    "send a message to David", "send it", "send it to David", "Tell me a joke", "What did David say?", "Scroll up",
    "Go to David", "Open Safari", "Read the last message", "I want you to open Safari", "What's on my screen?",
    "send a message", "Say it again", "Is there a send button?", "reply", "Switch the David",
    "Since the clav,",  # cut off mid-sentence
])
def test_not_a_message_with_its_words(utterance):
    intent = messaging.parse(utterance)
    assert intent is None or not intent.body


def test_the_model_is_only_asked_when_code_cannot_find_the_message():
    assert messaging.wants_message_extraction("Could you text David about being late")
    assert messaging.wants_message_extraction("ask David if he's coming")
    assert messaging.named("Could you text David about being late") == "David"
    for utterance in ("Tell David I'm late", "Read the last message", "Is there a send button?", "send a message"):
        assert not messaging.wants_message_extraction(utterance)
    call = ToolCall("a", "type_text", {"text": "I'm running late"})
    assert messaging.checked_body(call, "could you text David about me running late") == "I'm running late"
    invented = ToolCall("a", "type_text", {"text": "Sorry, traffic is terrible today"})
    assert messaging.checked_body(invented, "could you text David about me running late") is None


def test_open_chat_is_the_exact_title():
    assert messaging.is_open_chat("David", "David")
    assert messaging.is_open_chat("Clab", "Clav")
    assert not messaging.is_open_chat("David", "David & Clav")
    assert not messaging.is_open_chat("David", "Clav")


def messages_screen(name="Clav", value=""):
    return {
        "snapshot_id": "s_im", "app": "Messages", "window": name, "focused_id": 12,
        "text": f'APP Messages | WINDOW "{name}"\n[3] text "David, How are you?, 11:45 PM Clav, Damn, 1:18 AM"\n'
                f'[12] textarea "iMessage" value="{value}" (focused)',
        "elements": [
            {"id": 3, "role": "text", "label": "David, How are you?, 11:45 PM Clav, Damn, 1:18 AM", "states": ["selected"]},
            {"id": 12, "role": "textarea", "label": "iMessage", "value": value, "states": ["focused"]},
        ],
    }


class NoModel:
    requests = 0

    async def next_tool_call(self, messages, tools):
        self.requests += 1
        raise AssertionError("the model should not be asked")


class MessagesHelper:
    """A Messages window: typing fills the box, Return sends it (unless told to ignore Returns)."""

    def __init__(self, name="Clav", replies=(), ignored_returns=0):
        self.screen = messages_screen(name)
        self.replies = list(replies)
        self.ignored_returns = ignored_returns
        self.spoken, self.actions, self.sent = [], [], []
        self.brain = None

    async def snapshot(self):
        return copy.deepcopy(self.screen)

    async def frontmost(self):
        return {**copy.deepcopy(self.screen), "elements": []}

    async def hud(self, state, **fields):
        pass

    async def stop_speech(self):
        pass

    async def listen(self, mode):
        pass

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        if text.endswith("Say yes to continue.") and self.replies:
            reply = self.replies.pop(0)
            asyncio.get_running_loop().call_later(
                0.01, lambda: asyncio.ensure_future(self.brain.on_event({"type": "stt.final", "text": reply})))
        return speech_id

    async def request(self, path, payload):
        self.actions.append((path, payload))
        box = self.screen["elements"][1]
        if path == "/input/type":
            box["value"] = (box.get("value") or "") + payload["text"]
        if path == "/input/key" and payload["keys"] == "return":
            if self.ignored_returns:
                self.ignored_returns -= 1
            elif box["value"]:
                self.sent.append((self.screen["window"], box["value"]))
                box["value"] = ""
        return {"ok": True}


def hear(helper, utterance, model=None, **config):
    async def go():
        brain = helper.brain = Brain(helper, model or NoModel(), Config(log_path=Path("/dev/null"), settle_ms=0,
                                                                        **config), "Test")
        await brain.on_event({"type": "stt.final", "text": utterance, "stt_ms": 0})
        await asyncio.wait_for(brain.active, timeout=3)
        await asyncio.sleep(0.05)
        if brain.active is not None and not brain.active.done():
            await asyncio.wait_for(brain.active, timeout=3)
    asyncio.run(go())
    return helper


@pytest.mark.loop
@pytest.mark.parametrize("utterance, body", [
    ("I'm going to the hackathon in the morning", "I'm going to the hackathon in the morning"),
    ("tell him I'm going to the hackathon in the morning", "I'm going to the hackathon in the morning"),
    ("Send this: see you at 8", "see you at 8"),
    ("reply sounds good", "sounds good"),
    ("say happy birthday", "happy birthday"),
    ("Tell Clav I'm outside", "I'm outside"),
    ("Send to Clav, I'm going to the hackathon in the morning.", "I'm going to the hackathon in the morning."),
    ("Let them know I'll be late", "I'll be late"),
])
def test_sends_into_the_open_chat_with_no_model_call(utterance, body):
    helper = hear(MessagesHelper("Clav"), utterance)
    assert helper.sent == [("Clav", body)]
    assert helper.spoken[-1] == f"Sent to Clav: {body}."


@pytest.mark.loop
def test_a_question_for_a_friend_is_sent_after_one_yes():
    helper = hear(MessagesHelper("David", replies=["yes"]), "Are you coming tonight?")
    assert helper.spoken[0].startswith("Should I send Are you coming tonight? to David?")
    assert helper.sent == [("David", "Are you coming tonight?")]


@pytest.mark.loop
def test_no_to_the_one_question_sends_nothing():
    helper = hear(MessagesHelper("David", replies=["no"]), "Want to grab lunch tomorrow?")
    assert helper.sent == [] and helper.spoken[-1] == "Okay, I didn't send it."


@pytest.mark.loop
def test_a_misheard_name_asks_before_sending_to_the_open_chat():
    helper = hear(MessagesHelper("Clav", replies=["yes"]), "Send the collab, I'm going to hack it on in the morning.")
    assert "to Clav?" in helper.spoken[0]
    assert helper.sent == [("Clav", "I'm going to hack it on in the morning.")]


@pytest.mark.loop
def test_an_ignored_return_is_pressed_again():
    helper = hear(MessagesHelper("Clav", ignored_returns=1), "tell him I'm here")
    assert helper.sent == [("Clav", "I'm here")]
    assert [p["keys"] for path, p in helper.actions if path == "/input/key"] == ["return", "return"]


@pytest.mark.loop
def test_a_message_that_never_leaves_the_box_is_reported():
    helper = hear(MessagesHelper("Clav", ignored_returns=5), "tell him I'm here")
    assert helper.sent == []
    assert "didn't send" in helper.spoken[-1]


@pytest.mark.loop
def test_another_persons_chat_is_opened_first():
    helper = hear(MessagesHelper("Clav"), "Tell David I'm running late")
    # The search for David's chat starts; nothing is typed into Clav's box first.
    assert ("/input/key", {"keys": "cmd+f"}) in helper.actions
    assert helper.sent == []
    assert ("/input/type", {"text": "I'm running late"}) not in helper.actions


@pytest.mark.loop
def test_a_draft_already_in_the_box_is_not_overwritten_by_a_bare_sentence():
    helper = MessagesHelper("Clav")
    helper.screen["elements"][1]["value"] = "half written"

    class Stop:
        async def next_tool_call(self, messages, tools):
            return ToolCall("a", "done", {"summary": "Nothing to do."})

    hear(helper, "I'm going to the hackathon in the morning", Stop())
    assert helper.sent == []
