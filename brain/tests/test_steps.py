"""Type-into-a-field requests run as steps (steps.py): only finding the field may use the model."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from beacon_brain import steps
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain

ROOT = Path(__file__).resolve().parents[2]


def fixture(name):
    folder = "scripts/fixtures" if name.startswith("chrome") else "mock_helper/fixtures"
    return json.loads((ROOT / folder / f"{name}.json").read_text(encoding="utf-8"))


class NoModel:
    async def next_tool_call(self, messages, tools):
        raise AssertionError("the model was called")


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.tools = []

    async def next_tool_call(self, messages, tools):
        self.tools.append([tool["function"]["name"] for tool in tools])
        return self.calls.pop(0)


class PageHelper:
    """A page whose search field submits on Return (or only on its Go button when return_works=False)."""

    def __init__(self, screen, *, return_works=True):
        self.screen = copy.deepcopy(screen)
        self.return_works = return_works
        self.spoken, self.actions = [], []
        self.focus = None
        self.brain = None

    async def snapshot(self):
        return copy.deepcopy(self.screen)

    async def frontmost(self):
        return {**self.screen, "elements": []}

    async def apps(self):
        return {"running": [self.screen["app"]], "installed": []}

    async def hud(self, state, **fields):
        pass

    async def listen(self, mode):
        pass

    async def stop_speech(self):
        pass

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        return speech_id

    def element(self, element_id):
        return next(item for item in self.screen["elements"] if item["id"] == element_id)

    def submitted(self):
        field = self.element(self.focus) if self.focus else {}
        self.screen["window"] = f"Amazon.com : {field.get('value')} - Google Chrome"

    async def request(self, path, payload):
        self.actions.append((path, payload))
        if payload.get("action") == "focus":
            self.focus = payload["element_id"]
        if path == "/input/key" and payload["keys"] == "cmd+a" and self.focus:
            self.element(self.focus)["value"] = ""
        if path == "/input/type" and self.focus:
            field = self.element(self.focus)
            field["value"] = (field.get("value") or "") + payload["text"]
        if path == "/input/key" and payload["keys"] == "return" and self.return_works:
            self.submitted()
        if payload.get("action") == "press" and self.element(payload["element_id"])["label"] == "Go":
            self.submitted()
        return {"ok": True}

    def keys(self):
        return [payload["keys"] for path, payload in self.actions if path == "/input/key"]


def run_brain(helper, utterance, tmp_path, model=None):
    async def run():
        brain = helper.brain = Brain(helper, model or NoModel(), Config(log_path=tmp_path / "s.jsonl", settle_ms=0), "x")
        await brain.on_event({"type": "stt.final", "text": utterance})
        await asyncio.wait_for(brain.active, timeout=5)

    asyncio.run(run())


@pytest.mark.parametrize("utterance", [
    "In the Amazon search bar search for Christmas gifts.",
    "Search for Christmas gifts in the Amazon search bar.",
    "In the Amazon search bar, search for Christmas gifts and press enter.",
    # Heard on the live Mac: dictation turns a leading "In" into "and", and drops "bar".
    "and Amazon Search Bar search for Christmas gifts.",
    "and Amazon Search Bar, Search for Christmas gifts.",
    "and Amazon Search for Christmas gifts.",
    "In Amazon, search for Christmas gifts.",
])
def test_amazon_search_types_and_presses_return_in_code(tmp_path, utterance):
    helper = PageHelper(fixture("chrome_amazon"))
    run_brain(helper, utterance, tmp_path)
    assert ("/ax/act", {"snapshot_id": "s_amazon", "element_id": 13, "action": "focus"}) in helper.actions
    assert ("/input/type", {"text": "Christmas gifts"}) in helper.actions
    assert helper.keys() == ["return"]
    assert helper.spoken[-1] == "I searched for Christmas gifts."
    logged = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert [row["tool"] for row in logged] == ["type_text", "press_key"]
    assert all(row["ms"]["llm"] == 0 for row in logged)


def test_return_that_does_nothing_falls_back_to_the_go_button(tmp_path):
    helper = PageHelper(fixture("chrome_amazon"), return_works=False)
    run_brain(helper, "In the Amazon search bar search for Christmas gifts.", tmp_path)
    assert ("/ax/act", {"snapshot_id": "s_amazon", "element_id": 14, "action": "press"}) in helper.actions
    assert helper.spoken[-1] == "I searched for Christmas gifts."


def test_an_old_search_is_replaced_not_appended(tmp_path):
    screen = fixture("chrome_amazon")
    next(item for item in screen["elements"] if item["id"] == 13)["value"] = "lego"
    helper = PageHelper(screen)
    run_brain(helper, "Search for Christmas gifts in the Amazon search bar.", tmp_path)
    assert helper.keys() == ["cmd+a", "return"]
    assert helper.element(13)["value"] == "Christmas gifts"


def test_amazon_search_bar_on_another_site_uses_amazons_search_page(tmp_path):
    screen = fixture("chrome_article")
    helper = PageHelper(screen)
    run_brain(helper, "In the Amazon search bar search for Christmas gifts.", tmp_path)
    assert helper.keys()[0] == "cmd+l"
    assert ("/input/type", {"text": "amazon.com/s?k=christmas+gifts"}) in helper.actions


def test_an_unclear_field_is_the_only_step_the_model_sees(tmp_path):
    screen = fixture("chrome_checkout")  # several text fields, none called "notes"
    model = ScriptedModel(ToolCall("m1", "click", {"id": 22}))
    helper = PageHelper(screen)
    run_brain(helper, "Type 12 Main Street into the delivery field", tmp_path, model)
    assert model.tools == [["click"]]  # one call, with click as the only tool
    assert ("/ax/act", {"snapshot_id": screen["snapshot_id"], "element_id": 22, "action": "focus"}) in helper.actions
    assert ("/input/type", {"text": "12 Main Street"}) in helper.actions
    assert "return" not in helper.keys()


def test_type_and_press_enter_uses_the_focused_field(tmp_path):
    helper = PageHelper(fixture("chrome_checkout"))
    run_brain(helper, "Type hello world and press enter", tmp_path)
    assert ("/input/type", {"text": "hello world"}) in helper.actions
    assert helper.keys() == ["return"]


@pytest.mark.parametrize("utterance", ["Send hello to Emmett", "search for cats on amazon", "What is in the search bar?",
                                       "Type hello then click save", "and then search for cats"])
def test_other_requests_are_not_steps(utterance):
    assert steps.parse(utterance) is None


def test_the_loop_presses_return_when_the_model_stops_after_typing(tmp_path):
    """The model path (a field steps.py can't name): typing then confirm/done becomes Return in code."""
    screen = fixture("chrome_amazon")
    screen["elements"] = [item for item in screen["elements"] if item["id"] != 13]  # no field to name
    screen["elements"].append({**fixture("chrome_amazon")["elements"][12], "role": "group", "id": 13})
    model = ScriptedModel(ToolCall("m1", "type_text", {"text": "Christmas gifts"}),
                          ToolCall("m2", "confirm", {"summary": "I need to search for Christmas gifts."}),
                          ToolCall("m3", "done", {"summary": "Searched."}))
    helper = PageHelper(screen)
    run_brain(helper, "Search for Christmas gifts in the Amazon search bar.", tmp_path, model)
    assert "return" in helper.keys()


def test_a_repeated_confirm_is_not_approved_again(tmp_path):
    screen = fixture("chrome_amazon")
    confirm = ToolCall("c", "confirm", {"summary": "I need to search for Christmas gifts on Amazon."})
    model = ScriptedModel(confirm, confirm, confirm, confirm, ToolCall("d", "done", {"summary": "Stopped."}))
    helper = PageHelper(screen)
    run_brain(helper, "Find me something nice for my dad", tmp_path, model)
    assert len(model.tools) <= 3  # confirm, confirm (rejected), confirm (rejected) -> gives up
