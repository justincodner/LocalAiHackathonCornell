"""Mail reply and "send it" on a draft run in code (drafts.py), and compose's To-field reading."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from beacon_brain import compose, drafts
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain


def fixture(name):
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "mock_helper" / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def draft(body=""):
    screen = fixture("mail_reply_draft")
    for item in screen["elements"]:
        if item["id"] == 4:
            item["value"] = body
    return screen


class NoModel:
    """Fails the test if the model is asked anything."""

    async def next_tool_call(self, messages, tools):
        raise AssertionError("the model was called")


class MailHelper:
    """Inbox where Reply (button or cmd+R) opens the reply draft and Send closes it."""

    def __init__(self, screen, replies=()):
        self.screen = copy.deepcopy(screen)
        self.replies = list(replies)
        self.spoken, self.actions = [], []
        self.sends = 0
        self.brain = None

    async def snapshot(self):
        return copy.deepcopy(self.screen)

    async def hud(self, state, **fields):
        pass

    async def listen(self, mode):
        pass

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        if (text.endswith("say your answer.") or text.endswith("Say yes to continue.")) and self.replies:
            reply = self.replies.pop(0)
            asyncio.get_running_loop().call_later(
                0.01, lambda: asyncio.ensure_future(self.brain.on_event({"type": "stt.final", "text": reply})))
        return speech_id

    def element(self, element_id):
        return next(item for item in self.screen["elements"] if item["id"] == element_id)

    async def request(self, path, payload):
        self.actions.append((path, payload))
        label = self.element(payload["element_id"])["label"] if payload.get("action") == "press" else ""
        if path == "/input/type":
            body = self.element(4)
            body["value"] = (body.get("value") or "") + payload["text"]
        if self.screen["window"].startswith("Inbox") and (label == "Reply" or payload.get("keys") == "cmd+r"):
            self.screen = draft()
        elif self.screen["window"].startswith("Re:") and label == "Send":
            self.sends += 1
            self.screen = fixture("mail_inbox")
        return {"ok": True}

    def typed(self):
        return [payload["text"] for path, payload in self.actions if path == "/input/type"]


def run_brain(helper, utterance, tmp_path, **config):
    async def run():
        brain = helper.brain = Brain(helper, NoModel(), Config(log_path=tmp_path / "s.jsonl", settle_ms=0, **config), "x")
        await brain.on_event({"type": "stt.final", "text": utterance})
        await asyncio.wait_for(brain.active, timeout=3)

    asyncio.run(run())


def test_reply_opens_types_and_sends_by_default(tmp_path):
    helper = MailHelper(fixture("mail_inbox"))
    run_brain(helper, "Reply that I'll be ten minutes late", tmp_path)
    assert helper.typed() == ["I'll be ten minutes late"]
    assert helper.sends == 1
    assert helper.spoken[-1] == "Sent to Sarah Chen."
    assert not any("Say yes" in text or "say your answer" in text for text in helper.spoken)
    # Typed at the caret in the focused draft body, never into the inbox's Search field.
    assert not any(payload.get("element_id") == 14 for _, payload in helper.actions)


def test_reply_uses_cmd_r_without_a_reply_button(tmp_path):
    inbox = fixture("mail_inbox")
    inbox["elements"] = [item for item in inbox["elements"] if item["label"] != "Reply"]
    helper = MailHelper(inbox)
    run_brain(helper, "Reply that I'll be ten minutes late", tmp_path)
    assert helper.actions[0] == ("/input/key", {"keys": "cmd+r"})
    assert helper.sends == 1


@pytest.mark.parametrize("answer, sends", [("yes", 1), ("Send it", 1), ("no", 0), ("Stop", 0)])
def test_reply_reads_the_draft_back_when_confirmations_are_on(tmp_path, answer, sends):
    helper = MailHelper(fixture("mail_inbox"), [answer])
    run_brain(helper, "Reply that I'll be ten minutes late", tmp_path, confirm_sends=True)
    assert "Reply to Sarah Chen: I'll be ten minutes late. Say yes to continue." in helper.spoken
    assert helper.typed() == ["I'll be ten minutes late"]
    assert helper.sends == sends
    assert helper.spoken[-1] == ("Sent to Sarah Chen." if sends else "Okay, cancelled. The reply is still open.")


def test_reply_without_text_is_left_to_the_model():
    # "Reply to her" has no text: the model has the conversation (memory.Conversation) to work it out.
    assert drafts.flow_for("Reply to this email", fixture("mail_inbox")) is None
    assert drafts.flow_for("Reply to her", fixture("mail_inbox")) is None


@pytest.mark.parametrize("utterance", ["Send it", "send this", "Send the email", "okay, send it now"])
def test_send_it_on_a_filled_draft_presses_send(tmp_path, utterance):
    helper = MailHelper(draft("I'll be ten minutes late."))
    run_brain(helper, utterance, tmp_path)
    assert helper.typed() == []
    assert helper.sends == 1
    assert helper.spoken[-1] == "Sent to Sarah Chen."


def test_send_it_with_confirmations_on_is_its_own_approval(tmp_path):
    helper = MailHelper(draft("I'll be ten minutes late."))
    run_brain(helper, "Send it", tmp_path, confirm_sends=True)
    assert helper.sends == 1 and helper.typed() == []


def test_send_it_with_no_draft_open_does_nothing(tmp_path):
    helper = MailHelper(fixture("mail_inbox"))
    run_brain(helper, "Send it", tmp_path)
    assert helper.actions == [] and helper.sends == 0
    assert helper.spoken == ["There's no draft open to send."]


def test_flow_for_leaves_other_requests_to_the_model():
    inbox, empty, messages = fixture("mail_inbox"), draft(), {
        "app": "Messages", "focused_id": 12, "text": "", "window": "Emmett",
        "elements": [{"id": 12, "role": "textarea", "label": "iMessage", "value": "", "states": ["focused"]}]}
    assert drafts.flow_for("Reply that I'll be ten minutes late", inbox) == "reply"
    assert drafts.flow_for("Send it", draft("Hi")) == "send"
    assert drafts.flow_for("Send it", empty) is None  # an empty draft: the model decides
    assert drafts.flow_for("Send hello to Emmett", messages) is None
    assert drafts.flow_for("Reply hello", messages) is None  # no Reply control: compose.plan's job
    assert drafts.flow_for("Click Reply", inbox) is None
    assert drafts.flow_for("Reply to David that I'm late", inbox) is None  # not the open email
    assert drafts.flow_for("Don't reply yet", inbox) is None


def recorded(name):
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "contracts" / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def test_body_field_skips_address_and_search_fields():
    assert drafts.body_field(fixture("mail_inbox")) is None
    assert drafts.body_field(draft())["label"] == "Message"


def test_recorded_mail_screens():
    """Justin's recorded Mail: two Reply buttons, a "message body" group, a To token (U+FFFC)."""
    inbox, reply = recorded("mail_inbox"), recorded("mail_reply_draft")
    assert drafts.reply_opener(inbox).name == "click"
    assert drafts.body_field(inbox) is None
    assert drafts.body_field(reply)["label"] == "message body"
    assert drafts.flow_for("Send it", reply) == "send"  # the quoted email is in the body
    assert compose.recipient("Send it", reply) == "Sarah Chen"  # from "Sarah Chen <...> wrote:"


def test_recipient_reads_the_to_field():
    screen = draft()
    assert compose.recipient("Send it", screen) == "Sarah Chen"
    to = next(item for item in screen["elements"] if item["label"] == "To")
    to["value"] = "Sarah Chen <sarah@example.com>"
    assert compose.recipient("Send it", screen) == "Sarah Chen"
    assert compose.recipient("Send hello to Emmett") == "Emmett"
    assert compose.addressee(fixture("mail_inbox")) == "Sarah Chen"  # the selected email's sender


def test_confirmation_answers_are_never_the_message():
    asked = "Click Send in Mail. Say yes to continue."
    assert compose.message_for("Send it", [(asked, "yes")]) is None
    assert compose.message_for("Send it", [(asked, "Send it")]) is None
    assert compose.message_for("Send Emmett a message", [(compose.ASK_MESSAGE, "Yes")]) == "Yes"
    assert compose.message_for("Send Emmett a message", [("Who to?", "say I'm late")]) == "say I'm late"


def test_return_in_mail_is_not_a_send():
    assert not compose.return_sends(draft())
    assert compose.return_sends({"app": "Messages"})


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = iter(calls)

    async def next_tool_call(self, messages, tools):
        return next(self.calls)


def test_return_in_a_mail_body_does_not_claim_sent(tmp_path):
    async def run():
        helper = MailHelper(draft())
        model = ScriptedModel(
            ToolCall("a", "type_text", {"id": 4, "text": "I'll be late"}),
            ToolCall("b", "press_key", {"keys": "return"}),
            ToolCall("c", "done", {"summary": "Sent the reply to Sarah."}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl", settle_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": "Type I'll be late"})
        await asyncio.wait_for(brain.active, timeout=3)
        assert helper.sends == 0
        assert not helper.spoken[-1].startswith("Sent")
        assert "Nothing has been sent" in helper.spoken[-1]

    asyncio.run(run())
