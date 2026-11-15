import asyncio
import copy
import json
from pathlib import Path

import pytest

from beacon_brain import fastpath, locate
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain

ROOT = Path(__file__).resolve().parents[2]


def fixture(name):
    for folder in (ROOT / "scripts" / "fixtures", ROOT / "mock_helper" / "fixtures"):
        path = folder / f"{name}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(name)


class NoModel:
    async def next_tool_call(self, messages, tools):
        raise AssertionError("no model call expected")


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = iter(calls)

    async def next_tool_call(self, messages, tools):
        return next(self.calls)


class Helper:
    """Serves screens in order (the last one repeats) and records HUD, speech, reads and actions."""

    def __init__(self, *screens):
        self.screens = list(screens)
        self.huds, self.spoken, self.reads, self.actions = [], [], [], []
        self.brain = None

    async def snapshot(self):
        return copy.deepcopy(self.screens.pop(0) if len(self.screens) > 1 else self.screens[0])

    async def hud(self, state, **fields):
        self.huds.append((state, fields))

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        return speech_id

    async def read(self, snapshot_id, element_id):
        self.reads.append((snapshot_id, element_id))
        screen = self.screens[0]
        return next(e["label"] for e in screen["elements"] if e["id"] == element_id)

    async def request(self, path, payload):
        self.actions.append((path, payload))
        return {"ok": True}

    async def listen(self, mode):
        pass

    def highlights(self):
        return [f["highlight"]["element_id"] for _, f in self.huds if f.get("highlight")]


def run(helper, model, text):
    async def go():
        brain = helper.brain = Brain(helper, model, Config(log_path=Path("/dev/null"), settle_ms=0,
                                                           thinking_filler_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": text, "stt_ms": 0})
        await brain.active
    asyncio.run(go())


@pytest.mark.parametrize("text", ["Where do I pay?", "How do I pay?", "Where is the pay button?",
                                  "Where can I check out?", "Where's the submit button"])
def test_where_questions_route_to_the_screen(text):
    assert fastpath.is_question(text) and fastpath.needs_screen(text)


@pytest.mark.parametrize("text", ["Where is Paris?", "How do I make pasta?", "Where do I vote?"])
def test_general_where_questions_do_not(text):
    assert not fastpath.needs_screen(text)


def test_where_finds_unlabeled_pay_button_with_position():
    answer, element_id = locate.where(fixture("chrome_checkout"), "Where do I pay?")
    assert element_id == 38
    assert answer == "Bottom right. The button has no label; it is next to Order total $28.61."


def test_where_prefers_a_real_label_and_falls_back_when_nothing_matches():
    screen = fixture("chrome_checkout")
    at = next(i for i, e in enumerate(screen["elements"]) if e["id"] == 38)
    screen["elements"].insert(at, {"id": 99, "depth": 2, "role": "button", "label": "Place order", "states": [],
                               "bounds": [83, 140, 100, 30], "label_source": "title"})
    assert locate.where(screen, "Where is the checkout button?") == ("Top left. It is the Place order button.", 99)
    assert locate.where(screen, "Where is the rocket button?") is None
    assert locate.where(screen, "What is the order total?") is None


@pytest.mark.parametrize("text", ["Where do I pay?", "How do I pay?", "Where is the pay button?"])
def test_pay_question_rings_the_button_without_model_or_press(text):
    helper = Helper(fixture("chrome_checkout"))
    run(helper, NoModel(), text)
    assert helper.highlights() == [38]
    assert helper.spoken == ["Bottom right. The button has no label; it is next to Order total $28.61."]
    assert helper.actions == []


def test_reading_a_selected_mail_row_reads_the_message_and_rings_the_row():
    helper = Helper(fixture("mail_inbox"))
    run(helper, ScriptedModel(ToolCall("t1", "read_element", {"id": 8})), "Read the message from Sarah")
    assert helper.reads == [("s_0001", 15)]
    assert helper.highlights() == [8]
    assert "lunch tomorrow at noon" in helper.spoken[-1]
    assert helper.actions == []


def test_reading_an_unselected_mail_row_selects_it_first():
    before = fixture("mail_inbox")
    for element in before["elements"]:
        element["states"] = [s for s in element["states"] if s != "selected"]
        if element["id"] == 15:
            element["label"] = "Old message text that belongs to another conversation"
    after = fixture("mail_inbox")
    after["snapshot_id"] = "s_0002"
    helper = Helper(before, after)
    run(helper, ScriptedModel(ToolCall("t1", "read_element", {"id": 8})), "Read Sarah's email")
    assert ("/ax/act", {"snapshot_id": "s_0001", "element_id": 8, "action": "press"}) in helper.actions
    assert helper.reads == [("s_0002", 15)]
    assert helper.highlights() == [8]
    assert "lunch tomorrow at noon" in helper.spoken[-1]


def test_reading_other_elements_still_reads_them_with_a_ring():
    helper = Helper(fixture("mail_inbox"))
    run(helper, ScriptedModel(ToolCall("t1", "read_element", {"id": 9})), "Read the IT Desk subject")
    # Not selected: Beacon selects it, but the fake screen does not change, so the row is read.
    assert helper.reads[-1][1] == 9
    assert helper.highlights() == [9]


def test_message_body_skips_list_rows_and_window_title():
    screen = json.loads((ROOT / "contracts" / "fixtures" / "mail_inbox.json").read_text(encoding="utf-8"))
    assert locate.message_body(screen)["id"] == 14
