"""Continuous scrolling ("scroll through the results") runs in code until stop or the talk key."""

import asyncio
from pathlib import Path

import pytest

from beacon_brain import commands
from beacon_brain.config import Config
from beacon_brain.loop import Brain
from test_loop import ReplayHelper, ScriptedModel, fixture


@pytest.mark.parametrize("utterance, direction, speed", [
    ("Scroll through.", "down", "normal"),
    ("Scroll through the results.", "down", "normal"),
    ("scroll through the search results slowly", "down", "slow"),
    ("Scroll through the Amazon results", "down", "normal"),
    ("Keep scrolling", "down", "normal"),
    ("keep scrolling down", "down", "normal"),
    ("Keep on scrolling please", "down", "normal"),
    ("Scroll down slowly.", "down", "slow"),
    ("Slowly scroll down", "down", "slow"),
    ("scroll the page down slowly", "down", "slow"),
    ("Scroll up slowly", "up", "slow"),
    ("Start scrolling", "down", "normal"),
    ("Keep scrolling until I say stop", "down", "normal"),
    ("Can you scroll through these for me?", "down", "normal"),
    ("Scroll quickly", "down", "fast"),
    ("keep scrolling left", "left", "normal"),
    ("Keep scrolling right slowly", "right", "slow"),
    ("keep scrolling up this page", "up", "normal"),
    ("scroll down this window quickly", "down", "fast"),
    ("Auto scroll", "down", "normal"),
])
def test_continuous_scroll_phrasings(utterance, direction, speed):
    glide = commands.continuous_scroll(utterance)
    assert glide is not None and (glide.direction, glide.speed) == (direction, speed)


@pytest.mark.parametrize("utterance", [
    "Scroll down", "scroll down a bit", "Scroll up", "scroll up this page", "scroll left", "page down", "stop scrolling",
    "scroll through the results and click the first one", "scroll down to the reviews",
    "scroll through the results then open the second one", "Scroll to the bottom",
])
def test_one_page_scrolls_and_bigger_requests_are_not_continuous(utterance):
    assert commands.continuous_scroll(utterance) is None


def brain_for(helper, scroll_max_s=5.0):
    return Brain(helper, ScriptedModel(), Config(log_path=Path("/dev/null"), settle_ms=0, scroll_max_s=scroll_max_s),
                 "Test")


def chrome():
    return {**fixture("notes_list"), "app": "Google Chrome"}


def scrolls(helper):
    return [payload for path, payload in helper.actions if path == "/input/scroll"]


@pytest.mark.loop
def test_glide_scrolls_in_small_steady_steps_until_the_limit():
    async def go():
        helper = ReplayHelper(chrome())
        brain = helper.brain = brain_for(helper, scroll_max_s=0.6)
        await brain.on_event({"type": "stt.final", "text": "Scroll through the results", "stt_ms": 0})
        await asyncio.wait_for(brain.active, 3)
        return helper, brain

    helper, brain = asyncio.run(go())
    steps = scrolls(helper)
    assert len(steps) >= 6 and all(s["direction"] == "down" and 1 <= s["amount"] <= 60 for s in steps)
    total = sum(s["amount"] for s in steps)
    assert 60 <= total <= 120  # ~150 px/s for 0.6 s
    assert helper.snapshot_calls == 0 and brain.model.requests == []
    assert helper.spoken == ["Done scrolling."] and not brain.scrolling


@pytest.mark.loop
@pytest.mark.parametrize("event", [
    {"type": "stt.final", "text": "Stop", "stt_ms": 0},
    {"type": "listen.started"},  # the talk key
    {"type": "barge_in"},
])
def test_stop_or_talk_key_ends_the_glide(event):
    async def go():
        helper = ReplayHelper(chrome())
        helper.stop_speech = lambda: asyncio.sleep(0)
        brain = helper.brain = brain_for(helper)
        await brain.on_event({"type": "stt.final", "text": "Keep scrolling", "stt_ms": 0})
        glide = brain.active
        await asyncio.sleep(0.3)
        assert brain.scrolling
        await brain.on_event(event)
        assert glide.done() and not brain.scrolling
        if brain.active is not glide:
            await asyncio.wait_for(brain.active, 2)
        count = len(scrolls(helper))
        await asyncio.sleep(0.2)
        return helper, count

    helper, count = asyncio.run(go())
    assert count >= 3 and len(scrolls(helper)) == count  # nothing scrolls after the stop
    assert "Done scrolling." not in helper.spoken


@pytest.mark.loop
def test_talk_key_while_not_scrolling_still_just_listens():
    async def go():
        helper = ReplayHelper(chrome())
        brain = helper.brain = brain_for(helper)
        await brain.on_event({"type": "listen.started"})
        return helper, brain

    helper, brain = asyncio.run(go())
    assert brain.state == "listening" and helper.actions == []
