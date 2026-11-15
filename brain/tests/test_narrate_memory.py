"""B4/B3: narration templates (spec 7.5) and memory trimming (spec 6.4)."""

import json

import pytest

from beacon_brain import narrate
from beacon_brain.llm import ToolCall
from beacon_brain.memory import TaskMemory

SCREEN = {
    "snapshot_id": "s_0042",
    "app": "Mail",
    "text": "APP Mail | WINDOW \"Inbox\"\n[3] button \"Reply\"",
    "focused_id": 3,
    "elements": [{"id": 3, "role": "button", "label": "Reply", "value": None, "states": []}],
}


@pytest.mark.parametrize("name, args, spoken", [
    ("click", {"id": 3}, "Clicking Reply."),
    ("type_text", {"text": "late"}, "Typing."),
    ("type_text", {"text": "I will be about ten minutes late"}, "Typing. I will be about ten minutes late"),
    ("open_app", {"name": "Notes"}, "Opening Notes."),
    ("scroll", {"direction": "down"}, "Scrolling down."),
    ("press_key", {"keys": "cmd+return"}, "Pressing command return."),
])
def test_action_templates(name, args, spoken):
    assert narrate.action_text(ToolCall("c", name, args), SCREEN) == spoken


@pytest.mark.parametrize("raw, spoken", [
    ("The Submit button is located at element [5].", "The Submit button is located."),
    ("Click Reply [3] to answer.", "Click Reply to answer."),
    ("Submit (id [21]) is at the bottom.", "Submit is at the bottom."),
    ("Mail inbox. Three unread.", "Mail inbox. Three unread."),
])
def test_ids_are_never_spoken(raw, spoken):
    assert narrate.clean_speech(raw) == spoken


@pytest.mark.parametrize("speech", [
    "Do Not Disturb is on.", "Hold on, what are you looking at?", "Come on!", "Wi-Fi is off. Bluetooth is on.",
])
def test_ordinary_speech_is_never_mangled(speech):
    assert narrate.clean_speech(speech) == speech


@pytest.mark.parametrize("summary", [
    "Sarah sent you a lunch invite for Friday.",
    "IT Desk wrote about the deleted file.",
    "Your order from Acme shipped Monday.",
    "Mail, inbox. Newest is from Sarah Chen about lunch tomorrow.",
])
def test_describing_screen_content_is_not_a_claim(summary):
    assert narrate.honest_summary(summary, [], committed=False) == summary


@pytest.mark.parametrize("summary", [
    "Sent a message to Sarah Chen.", "I've sent your reply.", "The reply was sent successfully.",
    "Message deleted.", "Your email has been sent.",
])
def test_beacons_own_claims_are_checked(summary):
    assert narrate.honest_summary(summary, ["typed: hi"], committed=False) == "I typed: hi. Nothing has been sent yet."


def test_false_sent_claims_are_replaced_with_what_happened():
    actions = [narrate.past_action(ToolCall("c", "type_text", {"text": "I'll be late"}), SCREEN)]
    assert narrate.honest_summary("Sent a message to Sarah.", actions, committed=False) == (
        "I typed: I'll be late. Nothing has been sent yet."
    )
    assert narrate.honest_summary("Sent to Sarah Chen.", actions, committed=True) == "Sent to Sarah Chen."
    assert narrate.honest_summary("Opened a reply.", actions, committed=False) == "Opened a reply."
    assert narrate.honest_summary("Message deleted.", [], committed=False) == "Nothing was sent or changed."


def test_confirmation_names_the_target_and_app():
    assert narrate.confirm_text(ToolCall("c", "click", {"id": 3}), SCREEN) == "Click Reply in Mail"
    assert narrate.confirm_text(ToolCall("c", "press_key", {"keys": "cmd+q"}), SCREEN) == "Press cmd q in Mail"


def test_newest_snapshot_is_last_and_older_ones_are_stubbed():
    memory = TaskMemory("SYSTEM", "click reply")
    call = ToolCall("call_1", "click", {"id": 3})
    memory.add(call, {"ok": True}, "s_0041", "click reply")
    messages = memory.messages(SCREEN)

    assert messages[0] == {"role": "system", "content": "SYSTEM"}
    assert messages[-1]["content"].startswith(f"SCREEN:\n{SCREEN['text']}\n\nREQUEST: click reply\n")
    history = "\n".join(str(message.get("content")) for message in messages[1:-1])
    assert "[snapshot s_0041 omitted]" in history and "APP Mail" not in history
    assert messages[2]["tool_calls"][0]["function"] == {"name": "click", "arguments": json.dumps({"id": 3})}
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"ok": True})}


def test_earlier_conversation_sits_before_the_task_in_the_cached_prefix():
    earlier = "User: read the message from Sarah\nBeacon: Sarah asked about lunch."
    follow_up = TaskMemory("S", "open the second one", earlier).messages(SCREEN)
    assert follow_up[1] == {"role": "user", "content": f"EARLIER CONVERSATION:\n{earlier}"}
    assert len(follow_up) == 3


def test_completed_actions_are_listed_so_the_model_can_finish():
    memory = TaskMemory("S", "click reply")
    assert "ALREADY DONE" not in memory.messages(SCREEN)[-1]["content"]
    memory.add(ToolCall("c", "click", {"id": 3}), {"ok": True}, "s_1", "click reply", '"Reply"')
    memory.add(ToolCall("d", "click", {"id": 9}), {"ok": False}, "s_2", "click reply", '"Send"')
    last = memory.messages(SCREEN)[-1]["content"]
    assert 'ALREADY DONE: click "Reply".' in last and "Send" not in last
    assert "call done" in last


def test_an_answer_is_kept_with_the_question_and_the_request():
    memory = TaskMemory("S", "click the button")
    memory.answer("Which button: New Message or Reply?", "the reply one")
    last = memory.messages(SCREEN)[-1]["content"]
    assert 'REQUEST: click the button\nUSER ANSWERED "Which button: New Message or Reply?": the reply one' in last
    assert memory.latest_words == "the reply one"
    assert memory.all_words == "click the button the reply one"
    assert memory.one_line.startswith("click the button / USER ANSWERED")


def test_the_system_prefix_never_changes_between_steps():
    memory = TaskMemory("SYSTEM", "go")
    before = memory.messages(SCREEN)[0]
    memory.add(ToolCall("c", "scroll", {"direction": "down"}), {"ok": True}, "s_1", "go")
    assert memory.messages(SCREEN)[0] == before
