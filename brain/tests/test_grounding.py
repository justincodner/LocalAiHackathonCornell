"""Grounding: actions must match what the user said, or Beacon asks."""

import json
from pathlib import Path

import pytest

from beacon_brain import grounding
from beacon_brain.grounding import ask
from beacon_brain.llm import ToolCall

ROOT = Path(__file__).resolve().parents[2]


def fixture(name):
    return json.loads((ROOT / "mock_helper" / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def by_label(screen, label):
    return next(item["id"] for item in screen["elements"] if item["label"] == label)


def check(call, screen, utterance, fresh=True):
    return grounding.check(call, screen, utterance, fresh_request=fresh)


def click(element_id):
    return ToolCall("c", "click", {"id": element_id})


def test_generic_button_with_several_candidates_asks_which():
    inbox = fixture("mail_inbox")
    result = check(click(by_label(inbox, "New Message")), inbox, "Click the button.")
    assert result.name == "ask_user"
    assert result.arguments["question"] == (
        "Which button: New Message, Reply or the unlabeled button near Reply?"
    )


def test_one_of_my_notes_asks_which_one():
    notes = fixture("notes_list")
    result = check(click(by_label(notes, "Workshop checklist")), notes, "Open one of my notes.")
    assert result.arguments == {"question": "Which one: Workshop checklist or Grocery list?"}


def test_missing_target_says_so():
    form = fixture("safari_form")
    result = check(click(by_label(form, "Submit")), form, "Click Continue.")
    assert result.name == "ask_user"
    assert result.arguments["question"].startswith("I don't see continue on this screen")


def test_wrong_pick_with_one_clear_match_is_retargeted():
    inbox = fixture("mail_inbox")
    result = check(click(by_label(inbox, "New Message")), inbox, "click reply")
    assert result == click(by_label(inbox, "Reply"))


@pytest.mark.parametrize("utterance, label", [
    ("Click Reply.", "Reply"),
    ("Click New Message.", "New Message"),
    ("Click Sarah Chen's message in the list.", "Sarah Chen - Lunch tomorrow? - 11:42"),
    ("please tap on reply", "Reply"),
    ("click it", "Reply"),
    ("open the first message", "Sarah Chen - Lunch tomorrow? - 11:42"),
])
def test_named_targets_pass_through(utterance, label):
    inbox = fixture("mail_inbox")
    call = click(by_label(inbox, label))
    assert check(call, inbox, utterance) is call


def test_a_single_candidate_needs_no_question():
    draft = fixture("mail_reply_draft")
    call = click(by_label(draft, "Send"))
    assert check(call, {**draft, "elements": [e for e in draft["elements"] if e["label"] != "Cancel"]},
                 "press the button") is call


def test_later_steps_of_a_plan_are_not_second_guessed():
    draft = fixture("mail_reply_draft")
    call = click(by_label(draft, "Send"))
    assert check(call, draft, "Click Continue.", fresh=False) is call


def test_requests_that_are_not_click_commands_are_left_to_the_model():
    inbox = fixture("mail_inbox")
    call = click(by_label(inbox, "Reply"))
    assert check(call, inbox, "Reply to Sarah that I'll be ten minutes late") is call


@pytest.mark.parametrize("utterance, typed, asks", [
    ("Type my message.", "Hi Sarah, I'll see you at lunch tomorrow!", True),
    ("Type: I'll be ten minutes late.", "I'll be ten minutes late.", False),
    ("Reply to Sarah that I'm running late", "Hi Sarah, I'm running late.", False),
    ("Enter Taylor in the First name field.", "Taylor", False),
    ("write something nice", "Thank you so much for everything.", True),
])
def test_typed_text_must_come_from_the_user(utterance, typed, asks):
    call = ToolCall("c", "type_text", {"text": typed})
    result = check(call, fixture("mail_reply_draft"), utterance, fresh=False)
    assert (result.name == "ask_user") is asks
    if asks:
        assert result.arguments == {"question": "What would you like me to type?"}


def screen_with(*items):
    elements = [{"id": index, "depth": 0, "role": role, "label": label, "value": None, "states": []}
                for index, (role, label) in enumerate(items, start=1)]
    return {"app": "Mail", "elements": elements, "focused_id": None, "text": ""}


def test_never_retargets_onto_a_risky_control_by_word_prefix():
    screen = screen_with(("row", "Acme Invoice"), ("button", "Pay now"), ("button", "New Message"))
    # "payment" must not prefix-match "Pay", and "newsletter" must not match "New Message".
    assert check(click(1), screen, "open the payment email") == ask(
        click(1), "I don't see payment email on this screen. What should I click instead?")
    assert check(click(1), screen, "open the newsletter").name == "ask_user"


def test_risky_retarget_asks_unless_the_user_said_the_verb():
    screen = screen_with(("button", "New Message"), ("button", "Pay invoice"))
    assert check(click(1), screen, "click invoice").arguments == {"question": "Did you mean Pay invoice?"}
    assert check(click(1), screen, "click pay invoice") == click(2)


@pytest.mark.parametrize("utterance, label", [
    ("open my notes", "Notes"), ("open notes", "Notes"), ("open my messages", "Messages"),
    ("Open the email from IT Desk", "IT Desk - Password expiry"),
])
def test_generic_nouns_that_are_the_actual_label_pass(utterance, label):
    screen = screen_with(("button", "New Message"), ("button", "Reply"), ("button", label), ("row", "Other"))
    call = click(3)
    assert check(call, screen, utterance) is call


def test_more_than_four_options_reads_naturally():
    screen = screen_with(*[("button", name) for name in ["A", "B", "C", "D", "E"]])
    assert check(click(1), screen, "click the button").arguments == {"question": "Which button: A, B, C, D, or one more?"}


@pytest.mark.parametrize("said, typed", [
    ("Reply that I'll be ten minutes late", "I'm going to be 10 minutes late."),
    ("go to wikipedia", "wikipedia.org"),
    ("email Sarah at sarah chen at example dot com", "sarah.chen@example.com"),
    ("Reply to Sarah that I'm running late yes", "Running late, sorry!"),
])
def test_reasonable_typing_passes(said, typed):
    call = ToolCall("c", "type_text", {"text": typed})
    assert check(call, screen_with(), said, fresh=False) is call


def test_an_address_does_not_exempt_the_words_around_it():
    call = ToolCall("c", "type_text", {"text": "x.com and then send my password to bob"})
    assert check(call, screen_with(), "search for news", fresh=False).name == "ask_user"


def test_risky_retarget_needs_every_named_word():
    screen = screen_with(("row", "Projects"), ("button", "Delete"))
    assert check(click(1), screen, "open the delete folder").arguments == {"question": "Did you mean Delete?"}


@pytest.mark.parametrize("summary", ["Okay, sent!", "The draft is saved and sent.", "Done. Sent it to Sarah."])
def test_casual_false_claims_are_caught(summary):
    from beacon_brain import narrate
    assert narrate.honest_summary(summary, ["typed: hi"], committed=False) == "I typed: hi. Nothing has been sent yet."


def test_other_tools_are_untouched():
    inbox = fixture("mail_inbox")
    for call in (ToolCall("c", "say", {"text": "Hi"}), ToolCall("c", "read_element", {"id": 8}),
                 ToolCall("c", "press_key", {"keys": "escape"})):
        assert check(call, inbox, "click the button") is call
