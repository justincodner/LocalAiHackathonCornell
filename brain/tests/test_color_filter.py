"""macOS color filters for color blindness, run in code (color_filter.py). The OS is mocked."""

import asyncio
import json

import httpx
import pytest

from beacon_brain import color_filter
from beacon_brain.color_filter import Controller, Intent, choice, parse
from beacon_brain.config import Config
from beacon_brain.helper_client import HelperError
from beacon_brain.loop import Brain
from test_loop import ReplayHelper, ScriptedModel, fixture


class FakeDisplay:
    """The Helper's /display/color_filter endpoint over an in-memory setting."""

    def __init__(self, enabled=False, filter_name="grayscale", *, unsupported=False, sticks=True, missing=False):
        self.state = {"enabled": enabled, "filter": filter_name}
        self.unsupported, self.sticks, self.missing = unsupported, sticks, missing
        self.writes = []

    async def request(self, path, payload=None):
        assert path == "/display/color_filter"
        if self.missing:
            request = httpx.Request("GET", "http://127.0.0.1:8765" + path)
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        if self.unsupported:
            raise HelperError("action_unsupported", "not on this macOS")
        if payload is None:
            return {"ok": True, **self.state, "available": True}
        self.writes.append(payload)
        if not self.sticks:
            raise HelperError("action_failed", "The color filter change could not be verified")
        previous = dict(self.state)
        self.state["enabled"] = payload["enabled"]
        if "filter" in payload:
            self.state["filter"] = payload["filter"]
        return {"ok": True, **self.state, "previous": previous, "verified": True}


def run(coro):
    return asyncio.run(coro)


def no_question(_):
    raise AssertionError("should not ask")


def answering(reply):
    asked = []

    async def ask(question):
        asked.append(question)
        return reply
    ask.asked = asked
    return ask


# --- Intent recognition -------------------------------------------------------------------------

@pytest.mark.parametrize("utterance, expected", [
    ("Help me distinguish red and green.", Intent("choose")),
    ("I can't tell red from green", Intent("choose")),
    ("It's hard to see red and green", Intent("choose")),
    ("Can you turn on a red-green color blindness filter?", Intent("choose")),
    ("Turn on a color filter", Intent("choose", red_green=False)),
    ("I'm colorblind", Intent("choose", red_green=False)),
    ("Turn on the deuteranopia color filter.", Intent("enable", "deuteranopia")),
    ("Turn on deuteranopia", Intent("enable", "deuteranopia")),
    ("enable the protanopia filter", Intent("enable", "protanopia")),
    ("Switch to protanopia instead", Intent("enable", "protanopia")),
    ("Use the blue/yellow filter", Intent("enable", "tritanopia")),
    ("Make my screen grayscale", Intent("enable", "grayscale")),
    ("Try the other red-green filter.", Intent("other")),
    ("Switch to the other color filter", Intent("other")),
    ("Turn off the color filter.", Intent("off")),
    ("Disable the deuteranopia filter", Intent("off")),
    ("switch the color filter off", Intent("off")),
    ("Put my colors back to normal", Intent("restore")),
    ("Restore my previous color filter", Intent("restore")),
    ("Undo the color filter", Intent("restore")),
    ("Is the color filter on?", Intent("status")),
    # Before these were handled here, they reached the model, which refused to touch System Settings.
    ("Can you adjust my computer's colors for color blindness?", Intent("choose", red_green=False)),
    ("Adjust the screen colors for colorblindness", Intent("choose", red_green=False)),
    ("Change my display settings for color blindness", Intent("choose", red_green=False)),
    ("Make the screen colorblind friendly", Intent("choose", red_green=False)),
    ("Adjust the colors for red-green color blindness", Intent("choose")),
    ("Can you help me see colors better?", Intent("choose", red_green=False)),
    ("I have trouble with colors", Intent("choose", red_green=False)),
])
def test_requests_about_color_filters(utterance, expected):
    assert parse(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "What is on my screen?",
    "Click the red and green button",
    "What's the difference between the red and green lines on this chart?",
    "Filter my email by sender",
    "Turn it off",
    "Try the other one",
    "What is deuteranopia?",
    "Turn off the filter",
    "Open System Settings",
    "What is color blindness?",
    "How common is red-green color blindness?",
    "Search for color blindness tests",
    "Write an email about color blindness",
    "Can you read the article about color blindness",
])
def test_unrelated_or_ambiguous_requests_are_left_alone(utterance):
    assert parse(utterance) is None


@pytest.mark.parametrize("utterance, expected", [
    ("Try the other one", Intent("other")),
    ("the other one please", Intent("other")),
    ("Turn it off", Intent("off")),
    ("Turn off the filter", Intent("off")),
    ("put it back", Intent("restore")),
])
def test_short_follow_ups_refer_to_the_filter_just_changed(utterance, expected):
    assert parse(utterance, recent=True) == expected


@pytest.mark.parametrize("reply, expected", [
    ("Deuteranopia", "deuteranopia"),
    ("let's try protanopia", "protanopia"),
    ("the first one", "deuteranopia"),
    ("The second", "protanopia"),
    ("green red", "deuteranopia"),
    ("Red/Green", "protanopia"),
    ("either one", "either"),
    ("I don't know, you pick", "either"),
    ("No thanks", "decline"),
    ("neither", "decline"),
    ("bananas", None),
    ("tritanopia", None),  # not one of the two offered for red-green
])
def test_choice_replies(reply, expected):
    assert choice(reply) == expected


# --- Selecting, switching, disabling, restoring ----------------------------------------------------

def test_general_red_green_request_asks_and_never_picks_for_the_user():
    display = FakeDisplay()
    ask = answering(None)
    message = run(Controller(display).run(Intent("choose"), ask))
    assert ask.asked == [color_filter.CHOOSE_RED_GREEN]
    assert "deuteranopia or protanopia" in ask.asked[0]
    assert display.writes == []
    assert message == "Okay, I left your colors as they are."


def test_choosing_a_filter_turns_it_on_and_says_which():
    display = FakeDisplay()
    message = run(Controller(display).run(Intent("choose"), answering("protanopia")))
    assert display.state == {"enabled": True, "filter": "protanopia"}
    assert message.startswith("I turned on the protanopia filter (Red/Green in System Settings).")
    assert "try the other one" in message


def test_an_unclear_reply_changes_nothing():
    display = FakeDisplay()
    message = run(Controller(display).run(Intent("choose"), answering("bananas")))
    assert display.writes == [] and "left your colors as they are" in message


def test_either_starts_with_one_and_says_so_without_recommending():
    display = FakeDisplay()
    message = run(Controller(display).run(Intent("choose"), answering("either")))
    assert display.state["filter"] == "deuteranopia"
    assert message.startswith("I'll start with deuteranopia.") and "best" not in message


def test_named_filter_is_applied_without_asking():
    display = FakeDisplay()
    message = run(Controller(display).run(Intent("enable", "deuteranopia"), no_question))
    assert display.writes == [{"enabled": True, "filter": "deuteranopia"}]
    assert "deuteranopia" in message


def test_try_the_other_one_switches_between_red_green_filters():
    display = FakeDisplay()
    controller = Controller(display)
    run(controller.run(Intent("enable", "deuteranopia"), no_question))
    message = run(controller.run(Intent("other"), no_question))
    assert display.state == {"enabled": True, "filter": "protanopia"}
    assert message.startswith("Switched to the protanopia filter")
    run(controller.run(Intent("other"), no_question))
    assert display.state["filter"] == "deuteranopia"


def test_other_uses_a_red_green_filter_that_was_already_on():
    display = FakeDisplay(enabled=True, filter_name="protanopia")
    run(Controller(display).run(Intent("other"), no_question))
    assert display.state == {"enabled": True, "filter": "deuteranopia"}


def test_other_with_nothing_to_switch_from_asks_instead_of_guessing():
    display = FakeDisplay()
    ask = answering("no")
    run(Controller(display).run(Intent("other"), ask))
    assert ask.asked == [color_filter.CHOOSE_RED_GREEN] and display.writes == []


def test_turning_off_restores_the_users_filter_type_selection():
    display = FakeDisplay(enabled=False, filter_name="grayscale")
    controller = Controller(display)
    run(controller.run(Intent("enable", "deuteranopia"), no_question))
    message = run(controller.run(Intent("off"), no_question))
    assert display.writes[-1] == {"enabled": False, "filter": "grayscale"}
    assert display.state == {"enabled": False, "filter": "grayscale"}
    assert message == "I turned off the color filter. Your colors are back to normal."
    assert controller.prior is None


def test_turning_off_when_already_off_changes_nothing():
    display = FakeDisplay()
    message = run(Controller(display).run(Intent("off"), no_question))
    assert display.writes == [] and message == "The color filter is already off."


def test_restore_puts_back_a_filter_the_user_already_had_on():
    display = FakeDisplay(enabled=True, filter_name="tritanopia")
    controller = Controller(display)
    run(controller.run(Intent("enable", "protanopia"), no_question))
    off = run(controller.run(Intent("off"), no_question))
    assert "tritanopia" in off and "put my colors back" in off
    message = run(controller.run(Intent("restore"), no_question))
    assert display.state == {"enabled": True, "filter": "tritanopia"}
    assert message.startswith("I put your color filter back to the tritanopia filter")


def test_restore_without_a_beacon_change_just_turns_the_filter_off():
    display = FakeDisplay(enabled=True, filter_name="deuteranopia")
    run(Controller(display).run(Intent("restore"), no_question))
    assert display.state["enabled"] is False


def test_status_reports_the_current_filter():
    assert run(Controller(FakeDisplay(True, "deuteranopia")).run(Intent("status"), no_question)) == \
        "The deuteranopia filter (Green/Red in System Settings) is on."
    assert run(Controller(FakeDisplay()).run(Intent("status"), no_question)) == "No color filter is on."


# --- Failures are reported, never claimed as success ----------------------------------------------

def test_unverified_change_is_reported_as_a_failure():
    display = FakeDisplay(sticks=False)
    controller = Controller(display)
    message = run(controller.run(Intent("enable", "deuteranopia"), no_question))
    assert "couldn't confirm" in message and "I turned on" not in message
    assert controller.failed and controller.applied is None


def test_helper_answer_that_does_not_match_is_a_failure():
    class Liar(FakeDisplay):
        async def request(self, path, payload=None):
            if payload is None:
                return {"ok": True, "enabled": False, "filter": "grayscale"}
            return {"ok": True, "enabled": True, "filter": "protanopia", "verified": True}
    message = run(Controller(Liar()).run(Intent("enable", "deuteranopia"), no_question))
    assert "couldn't confirm" in message


@pytest.mark.parametrize("display", [FakeDisplay(unsupported=True), FakeDisplay(missing=True)])
def test_unsupported_system_says_so(display):
    controller = Controller(display)
    message = run(controller.run(Intent("enable", "deuteranopia"), no_question))
    assert message == color_filter.unavailable_message() and controller.failed


def test_turn_off_failure_is_reported():
    display = FakeDisplay(enabled=True, filter_name="deuteranopia", sticks=False)
    message = run(Controller(display).run(Intent("off"), no_question))
    assert message.startswith("I tried to turn off the color filter, but I couldn't confirm")


# --- Through the Brain ---------------------------------------------------------------------------

class DisplayHelper(ReplayHelper):
    def __init__(self, display):
        super().__init__(fixture("safari_form"))
        self.display = display

    async def request(self, path, payload=None):
        if path == "/display/color_filter":
            return await self.display.request(path, payload)
        return await super().request(path, payload)


def brain_with(display, tmp_path):
    helper = DisplayHelper(display)
    model = ScriptedModel()
    brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
    return helper, model, brain


async def say(brain, text):
    await brain.on_event({"type": "stt.final", "text": text})
    await brain.active


@pytest.mark.loop
def test_voice_flow_ask_choose_switch_and_turn_off(tmp_path):
    async def go():
        display = FakeDisplay()
        helper, model, brain = brain_with(display, tmp_path)

        await brain.on_event({"type": "stt.final", "text": "Help me distinguish red and green."})
        await helper.clarifying.wait()
        assert "deuteranopia or protanopia" in helper.spoken[-1]
        await brain.on_event({"type": "stt.final", "text": "deuteranopia"})
        await brain.active
        assert display.state == {"enabled": True, "filter": "deuteranopia"}
        assert helper.spoken[-1].startswith("I turned on the deuteranopia filter")

        await say(brain, "Try the other one")
        assert display.state["filter"] == "protanopia"
        assert helper.spoken[-1].startswith("Switched to the protanopia filter")

        await say(brain, "Turn off the color filter")
        assert display.state["enabled"] is False
        assert model.requests == [] and helper.snapshot_calls == 0
        records = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
        assert [r["args"]["intent"] for r in records] == ["choose", "other", "off"]
        assert all(r["ok"] for r in records)

    asyncio.run(go())


@pytest.mark.loop
def test_adjusting_colors_for_color_blindness_never_reaches_the_model(tmp_path):
    """The model refused this ("not authorized to modify system settings") before the fast path knew it."""
    async def go():
        display = FakeDisplay()
        helper, model, brain = brain_with(display, tmp_path)
        await brain.on_event({"type": "stt.final", "text": "Adjust the computer's colors for color blindness."})
        await helper.clarifying.wait()
        assert helper.spoken[-1].startswith(color_filter.CHOOSE_ANY)
        await brain.on_event({"type": "stt.final", "text": "protanopia"})
        await brain.active
        assert display.state == {"enabled": True, "filter": "protanopia"}
        assert model.requests == [] and helper.snapshot_calls == 0

    asyncio.run(go())


@pytest.mark.loop
def test_bare_other_one_is_not_a_color_request_after_something_else(tmp_path):
    async def go():
        display = FakeDisplay()
        helper, model, brain = brain_with(display, tmp_path)
        await say(brain, "Turn on deuteranopia")
        await say(brain, "What time is it?")
        assert color_filter.parse("Try the other one", recent=brain.color_filter.recent(brain.task_number + 1)) is None

    asyncio.run(go())


@pytest.mark.loop
def test_failure_is_spoken_and_logged(tmp_path):
    async def go():
        display = FakeDisplay(sticks=False)
        helper, _, brain = brain_with(display, tmp_path)
        await say(brain, "Turn on the protanopia color filter")
        assert "couldn't confirm" in helper.spoken[-1]
        assert json.loads((tmp_path / "s.jsonl").read_text())["ok"] is False

    asyncio.run(go())


@pytest.mark.loop
def test_mock_helper_serves_the_color_filter_endpoint():
    from fastapi.testclient import TestClient
    from mock_helper.server import create_app

    client = TestClient(create_app())
    assert client.get("/display/color_filter").json()["enabled"] is False
    body = client.post("/display/color_filter", json={"enabled": True, "filter": "deuteranopia"}).json()
    assert body["ok"] and body["verified"] and body["filter"] == "deuteranopia"
    assert body["previous"]["enabled"] is False
    assert client.post("/display/color_filter", json={"enabled": True}).json()["error"]["code"] == "bad_request"
