"""The HUD's ⋯ menu loads and unloads the Bonsai 4B question model at runtime."""
import asyncio

import pytest

from beacon_brain import question_switch
from beacon_brain.config import Config
from beacon_brain.llm import BonsaiError
from beacon_brain.loop import Brain
from beacon_brain.question_switch import QuestionModelSwitch

from test_loop import ReplayHelper, ScriptedModel, fixture


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    async def discover_model(self):
        if self.fail:
            raise BonsaiError("no model")
        return "4B"

    async def aclose(self):
        self.closed = True


class RecordingHelper(ReplayHelper):
    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.hud_fields = []

    async def hud(self, state, **fields):
        self.hud_states.append(state)
        self.hud_fields.append(fields)


def make_brain(tmp_path, *, up=False, fail=False):
    helper = RecordingHelper(fixture("safari_form"))
    brain = helper.brain = Brain(helper, ScriptedModel(), Config(log_path=tmp_path / "s.jsonl"), "Test instruction")
    calls = {"launch": 0, "kill": 0, "clients": []}

    async def is_up():
        return up

    async def launch():
        calls["launch"] += 1

    def make_client(config, url, question=False):
        assert url == question_switch.URL and question
        client = FakeClient(fail)
        calls["clients"].append(client)
        return client

    brain.question_switch = QuestionModelSwitch(brain.config, make_client, is_up=is_up, launch=launch,
                                                stop=lambda: calls.__setitem__("kill", calls["kill"] + 1))
    return helper, brain, calls


def test_toggle_on_loads_4b_and_reports_loading_then_on(tmp_path):
    async def run():
        helper, brain, calls = make_brain(tmp_path)
        await brain.on_event({"type": "model.question", "enabled": True})
        await asyncio.wait_for(brain.switching, timeout=2)
        assert calls["launch"] == 1
        assert brain.question_model is calls["clients"][0]
        states = [f.get("question_model") for f in helper.hud_fields if "question_model" in f]
        assert states == ["loading", True]
        assert question_switch.ENABLED in helper.spoken

        await brain.on_event({"type": "model.question", "enabled": False})
        await asyncio.wait_for(brain.switching, timeout=2)
        assert brain.question_model is None
        assert calls["clients"][0].closed and calls["kill"] == 1
        assert [f["question_model"] for f in helper.hud_fields if "question_model" in f][-1] is False

    asyncio.run(run())


def test_already_running_4b_is_reused(tmp_path):
    async def run():
        helper, brain, calls = make_brain(tmp_path, up=True)
        await brain.on_event({"type": "model.question", "enabled": True})
        await asyncio.wait_for(brain.switching, timeout=2)
        assert calls["launch"] == 0 and brain.question_model is not None

    asyncio.run(run())


def test_failed_load_stays_on_2b(tmp_path):
    async def run():
        helper, brain, calls = make_brain(tmp_path, fail=True)
        await brain.on_event({"type": "model.question", "enabled": True})
        await asyncio.wait_for(brain.switching, timeout=2)
        assert brain.question_model is None
        assert helper.hud_states[-1] == "error" and helper.hud_fields[-1]["question_model"] is False
        assert calls["kill"] == 1, "a half-started 4B is stopped again to free memory"

    asyncio.run(run())
