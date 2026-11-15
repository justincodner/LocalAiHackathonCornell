"""Demo sweep: Mail reply (demo steps 4, 5, 8) and single commands, on the real local Bonsai.

Multi-utterance scenarios run several requests on one Brain, like a live session. A fake
Helper serves fixture screens (pressing Reply opens the mail_reply_draft screen) and
records every action; nothing touches the real Mac.

    brain/.venv/bin/python scripts/demo_sweep_actions.py [-n 3] [-j 2] [name filters...]
"""

import os
import argparse
import asyncio
import copy
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_scenarios import FakeHelper, load, messages_screen  # noqa: E402
# Pin only Bonsai's last slot, so a run never evicts the live Beacon's cached prompts (config.llm_slots).
os.environ.setdefault("BEACON_LLM_SLOTS", "last")
from beacon_brain.config import Config  # noqa: E402
from beacon_brain.loop import Brain  # noqa: E402
from beacon_brain.main import make_client  # noqa: E402


class MailHelper(FakeHelper):
    """Mail inbox where Reply (button or cmd+R) opens the reply draft and Send returns to the inbox."""

    def __init__(self, screen, replies):
        super().__init__(screen, replies)
        self.sends = 0
        self.stop_calls = 0

    async def stop_speech(self):
        self.stop_calls += 1

    def _switch(self, name: str):
        self.screen = copy.deepcopy(load(name))
        self.focus = self.screen.get("focused_id")
        if self.brain is not None:
            asyncio.ensure_future(self.brain.on_event({"type": "app.changed"}))

    async def request(self, path, payload):
        label = ""
        if path == "/ax/act" and payload.get("action") == "press":
            label = ((self._element(payload["element_id"]) or {}).get("label") or "").casefold()
        result = await super().request(path, payload)
        keys = payload.get("keys", "").casefold() if path == "/input/key" else ""
        if self.screen["app"] == "Mail" and self.screen["window"].startswith("Inbox") and (
                label == "reply" or keys in {"cmd+r", "command+r"}):
            self._switch("mail_reply_draft")
        elif self.screen["window"].startswith("Re:") and (label == "send" or keys in {"cmd+shift+d"}):
            self.sends += 1
            self._switch("mail_inbox")
        return result


def draft_with_body() -> dict:
    screen = copy.deepcopy(load("mail_reply_draft"))
    body = "I'll be ten minutes late."
    for element in screen["elements"]:
        if element["id"] == 4:
            element["value"] = body
    screen["text"] = screen["text"].replace('[4] textarea "Message" value=""', f'[4] textarea "Message" value="{body}"')
    return screen


def focused_form() -> dict:
    screen = copy.deepcopy(load("safari_form"))
    screen["focused_id"] = 4
    for element in screen["elements"]:
        if element["id"] == 4:
            element["states"] = ["focused"]
    screen["text"] = screen["text"].replace('WINDOW "Contact us"', 'WINDOW "Contact us" | FOCUS [4]').replace(
        '[4] textarea "Message" value=""', '[4] textarea "Message" value="" (focused)')
    return screen


@dataclass
class Step:
    text: str
    wait: bool = True  # False: the next step is spoken 1 s later, while this one still runs


@dataclass
class Scenario:
    name: str
    screen: Callable[[], dict]
    steps: list[Step]
    check: Callable[["MailHelper", dict], str | None]
    replies: list[str] = field(default_factory=list)
    confirm_sends: bool = False


def expect(ok: bool, why: str) -> str | None:
    return None if ok else why


def body_typed(h) -> str:
    texts = [p["text"] for path, p in h.actions if path == "/input/type"]
    texts += [str(p["value"]) for path, p in h.actions if path == "/ax/act" and p.get("action") == "set_value"]
    return " ".join(texts)


def late_typed(h) -> bool:
    t = body_typed(h).casefold()
    return "late" in t and ("ten" in t or "10" in t) and "reply" not in t


def screen_acts(h):
    return [a for a in h.actions if a[0] != "/ax/read"]


def reply_default(h, m):
    return expect("reply" in [p.casefold() for p in h.pressed()] or "cmd+r" in h.keys(), f"no reply opened: {h.actions}") \
        or expect(late_typed(h), f"typed {body_typed(h)!r}") \
        or expect(h.sends == 1, f"sends {h.sends}")


def reply_confirm(h, m):
    return expect(late_typed(h), f"typed {body_typed(h)!r}") \
        or expect(h.asked >= 1 and "say yes to continue" in h.said(), f"did not ask: {h.spoken}") \
        or expect(h.sends == 1, f"sends {h.sends} after yes")


def reply_confirm_no(h, m):
    return expect(late_typed(h), f"typed {body_typed(h)!r}") \
        or expect(h.asked >= 1, f"did not ask: {h.spoken}") \
        or expect(h.sends == 0, f"sent after no ({h.sends})")


def sent_to_sarah(h, m):
    return expect(h.sends == 1, f"sends {h.sends}") or expect("sarah" in h.said(), f"no 'Sarah' in {h.spoken}")


def stop_during(h, m):
    after = m["actions_at"][1] if len(m["actions_at"]) > 1 else len(h.actions)
    later = h.actions[after:]
    return expect(not [a for a in later if a[0] != "/ax/read"], f"acted after stop: {later}") \
        or expect(h.sends == 0, "sent after stop")


SCENARIOS = [
    # A. Demo step 4 and 5 on Mail.
    Scenario("A1 reply default config", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late")], reply_default),
    Scenario("A2 reply confirm on, yes", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late")], reply_confirm, replies=["yes"], confirm_sends=True),
    Scenario("A3 reply confirm on, send it", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late")], reply_confirm, replies=["Send it"], confirm_sends=True),
    Scenario("A4 reply confirm on, no", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late")], reply_confirm_no, replies=["no"], confirm_sends=True),
    Scenario("A5 send it on draft (fresh)", draft_with_body, [Step("Send it")], sent_to_sarah),
    Scenario("A6 send it on draft (confirm on)", draft_with_body, [Step("Send it")], sent_to_sarah,
             replies=["yes"], confirm_sends=True),
    Scenario("A7 step4 then step5 default", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late"), Step("Send it")],
             lambda h, m: expect(h.sends == 1, f"sends {h.sends} (want exactly one)")),
    Scenario("A8 delete all emails default (step 8)", lambda: load("mail_inbox"),
             [Step("Delete all my emails")],
             lambda h, m: expect(h.asked >= 1, f"no confirmation; actions {screen_acts(h)}"), replies=["no"]),
    Scenario("A9 delete all emails confirm on (step 8)", lambda: load("mail_inbox"),
             [Step("Delete all my emails")],
             lambda h, m: expect(h.asked >= 1, f"no confirmation; actions {screen_acts(h)}")
             or expect("cancel" in h.said(), f"no 'cancelled' in {h.spoken}"), replies=["no"], confirm_sends=True),
    # B. Single commands.
    Scenario("B1 click submit", lambda: load("safari_form"), [Step("Click Submit")],
             lambda h, m: expect(h.pressed() == ["Submit"], f"pressed {h.pressed()}")),
    Scenario("B2 type hello", focused_form, [Step("Type hello")],
             lambda h, m: expect(body_typed(h).strip().casefold().rstrip(".!") == "hello", f"typed {body_typed(h)!r}")
             or expect(not h.pressed() and "return" not in h.keys(), f"extra {screen_acts(h)}")),
    Scenario("B3 open notes (from Mail)", lambda: load("mail_inbox"), [Step("Open Notes")],
             lambda h, m: expect(any(a == ("/app/open", {"name": "Notes"}) for a in h.actions), f"actions {h.actions}")),
    Scenario("B4 open notes (from Safari)", lambda: load("safari_form"), [Step("Open Notes")],
             lambda h, m: expect(any(a[0] == "/app/open" and a[1].get("name", "").casefold() == "notes"
                                     for a in h.actions), f"actions {h.actions}")),
    Scenario("B5 scroll down", lambda: load("chrome_article"), [Step("Scroll down")],
             lambda h, m: expect([a[1].get("direction") for a in h.actions if a[0] == "/input/scroll"] == ["down"],
                                 f"actions {h.actions}")),
    Scenario("B6 scroll up", lambda: load("chrome_article"), [Step("Scroll up")],
             lambda h, m: expect([a[1].get("direction") for a in h.actions if a[0] == "/input/scroll"] == ["up"],
                                 f"actions {h.actions}")),
    Scenario("B7 stop alone", lambda: load("mail_inbox"), [Step("Stop")],
             lambda h, m: expect(not h.actions and h.stop_calls == 1, f"actions {h.actions}, stop {h.stop_calls}")),
    Scenario("B8 stop during reply task", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late", wait=False), Step("Stop")], stop_during),
    Scenario("B9 stop as answer to question", lambda: messages_screen(), [Step("Send a message to Emmett")],
             lambda h, m: expect(h.asked == 1 and not h.typed() and not h.keys(), f"asked {h.asked} actions {h.actions}"),
             replies=["Stop"]),
    Scenario("B10 stop as answer to confirm", lambda: load("mail_inbox"),
             [Step("Reply that I'll be ten minutes late")], reply_confirm_no, replies=["Stop"], confirm_sends=True),
    Scenario("B11 repeat that", lambda: load("notes_list"),
             [Step("What's on my screen?"), Step("Repeat that")],
             lambda h, m: expect(len(h.spoken) > len(m["spoken_at"][1]) and len(m["spoken_at"][1]) > 0
                                 and h.spoken[-1] == m["spoken_at"][1][-1], f"said {h.spoken}")),
]


async def wait_idle(brain: Brain, timeout: float):
    while True:
        task = brain.active
        if task is None:
            return
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        await asyncio.sleep(0.2)
        if brain.active is task or brain.active.done():
            return


async def run_one(s: Scenario, base: Config, prompt, model, qmodel):
    config = Config(**{**base.__dict__, "confirm_sends": s.confirm_sends})
    helper = MailHelper(s.screen(), s.replies)
    brain = helper.brain = Brain(helper, model, config, prompt, qmodel)
    marks = {"actions_at": [], "spoken_at": [], "step_s": []}
    started = time.perf_counter()
    problem = None
    try:
        for i, step in enumerate(s.steps):
            marks["actions_at"].append(len(helper.actions))
            marks["spoken_at"].append(list(helper.spoken))
            t0 = time.perf_counter()
            await brain.on_event({"type": "stt.final", "text": step.text, "stt_ms": 0})
            if step.wait or i == len(s.steps) - 1:
                await wait_idle(brain, config.task_timeout_s + 5)
            else:
                await asyncio.sleep(1.0)
            marks["step_s"].append(round(time.perf_counter() - t0, 1))
        await wait_idle(brain, config.task_timeout_s + 5)
    except TimeoutError:
        problem = "timed out"
    seconds = time.perf_counter() - started
    if problem is None:
        try:
            problem = s.check(helper, marks)
        except Exception as error:  # noqa: BLE001
            problem = f"check crashed: {error!r}"
    return problem, seconds, helper, marks


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=3)
    parser.add_argument("-j", type=int, default=2)
    parser.add_argument("filters", nargs="*")
    args = parser.parse_args()
    env = Config.from_environment()
    log = Path(tempfile.mkdtemp()) / "sweep.jsonl"
    base = Config(**{**env.__dict__, "log_path": log, "settle_ms": 0, "reply_timeout_s": 3, "thinking_filler_ms": 0})
    prompt = base.prompt_path.read_text(encoding="utf-8").strip()
    model = make_client(base, base.llm_url)
    qmodel = make_client(base, base.llm_question_url, question=True) if base.llm_question_url else None
    chosen = [s for s in SCENARIOS if not args.filters or any(f.casefold() in s.name.casefold() for f in args.filters)]
    gate = asyncio.Semaphore(args.j)

    async def guarded(s, k):
        async with gate:
            return s, k, await run_one(s, base, prompt, model, qmodel)

    results = await asyncio.gather(*(guarded(s, k) for s in chosen for k in range(args.n)))
    by_name: dict[str, list] = {}
    for s, k, r in results:
        by_name.setdefault(s.name, []).append(r)
    total_pass = 0
    for s in chosen:
        runs = by_name[s.name]
        passes = sum(r[0] is None for r in runs)
        total_pass += passes
        times = [r[1] for r in runs]
        print(f"\n== {s.name}: {passes}/{len(runs)} pass, median {statistics.median(times):.1f}s "
              f"(min {min(times):.1f}, max {max(times):.1f})  steps={[st.text for st in s.steps]}"
              f"{' confirm_sends' if s.confirm_sends else ''}")
        for problem, seconds, h, marks in runs:
            acts = [(p, {k: v for k, v in a.items() if k != 'snapshot_id'}) for p, a in screen_acts(h)]
            print(f"  {'PASS' if problem is None else 'FAIL'} {seconds:5.1f}s step_s={marks['step_s']} sends={h.sends}"
                  f" asked={h.asked}")
            print(f"     said: {h.spoken}")
            print(f"     acts: {acts}")
            if problem:
                print(f"     why:  {problem}")
    print(f"\n{total_pass}/{len(results)} runs passed. Step log: {log}")
    await model.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
