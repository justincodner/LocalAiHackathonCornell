"""Demo sweep, READ path (DEMO_SCRIPT.md steps 2, 3 and 6) on the real local Bonsai.

Uses the loop_scenarios harness (FakeHelper + fixtures) but records every /hud call so the
highlight ring can be checked. Each scenario runs several times because the model is stochastic.

    brain/.venv/bin/python scripts/demo_sweep_read.py            # all, 3 runs each
    RUNS=5 brain/.venv/bin/python scripts/demo_sweep_read.py pay # names containing "pay"
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_scenarios import (  # noqa: E402
    Config, FakeHelper, Scenario, expect, load, make_client, no_actions,
)
import loop_scenarios  # noqa: E402
# Pin only Bonsai's last slot, so a run never evicts the live Beacon's cached prompts (config.llm_slots).
os.environ.setdefault("BEACON_LLM_SLOTS", "last")
from beacon_brain.loop import Brain  # noqa: E402

RUNS = int(os.environ.get("RUNS", "3"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "2"))


class HudHelper(FakeHelper):
    """FakeHelper that keeps the /hud calls (state, fields) instead of dropping them."""

    def __init__(self, screen, replies):
        super().__init__(screen, replies)
        self.huds: list[tuple[str, dict]] = []

    async def hud(self, state, **fields):
        self.huds.append((state, fields))

    def highlights(self) -> list[int]:
        return [f["highlight"]["element_id"] for _, f in self.huds if f.get("highlight")]


def mentions(h, *words) -> bool:
    return any(w in h.said() for w in words)


def step2(h):
    return no_actions(h) or expect(
        mentions(h, "sarah", "lunch", "it desk", "password", "inbox", "unread"),
        "summary did not mention the inbox contents")


def step3(h):
    problems = []
    if 8 not in h.highlights():
        problems.append(f"no highlight on Sarah's row [8] (highlights {h.highlights()})")
    if not mentions(h, "lunch", "noon"):
        problems.append("message content not read")
    bad = [l for l in h.pressed() if "sarah" not in l.casefold()]
    if bad or h.typed() or h.keys():
        problems.append(f"unwanted actions {h.actions}")
    return "; ".join(problems) or None


def step6(h):
    problems = []
    if 38 not in h.highlights():
        problems.append(f"no highlight on unlabeled pay button [38] (highlights {h.highlights()})")
    if h.pressed():
        problems.append(f"pressed {h.pressed()}")
    if not mentions(h, "order total"):
        problems.append("did not say it is next to Order total")
    if not mentions(h, "no label", "unlabeled", "unlabelled"):
        problems.append("did not say the button has no label")
    if not mentions(h, "bottom right", "bottom-right", "lower right"):
        problems.append("did not give the location (bottom right)")
    return "; ".join(problems) or None


SCENARIOS = [
    Scenario("s2 screen summary", lambda: load("mail_inbox"), "What's on my screen?", step2),
    Scenario("s2 screen variant right now", lambda: load("mail_inbox"), "What is on the screen right now?", step2),
    Scenario("s2 screen variant describe", lambda: load("mail_inbox"), "Describe my screen.", step2),
    Scenario("s3 read sarah", lambda: load("mail_inbox"), "Read the message from Sarah", step3),
    Scenario("s3 read sarah variant possessive", lambda: load("mail_inbox"), "Read Sarah's email", step3),
    Scenario("s3 read sarah variant full name", lambda: load("mail_inbox"), "Read me the email from Sarah Chen.", step3),
    Scenario("s6 where pay", lambda: load("chrome_checkout"), "Where do I pay?", step6),
    Scenario("s6 pay variant how", lambda: load("chrome_checkout"), "How do I pay?", step6),
    Scenario("s6 pay variant button", lambda: load("chrome_checkout"), "Where is the pay button?", step6),
]


async def main(filters: list[str]) -> int:
    loop_scenarios.FakeHelper = HudHelper  # run_one builds its helper from this name
    base = Config.from_environment()
    log = Path(tempfile.mkdtemp()) / "sweep_read.jsonl"
    config = Config(**{**base.__dict__, "log_path": log, "settle_ms": 0, "reply_timeout_s": 3,
                       "thinking_filler_ms": 0})
    prompt = config.prompt_path.read_text(encoding="utf-8").strip()
    model = make_client(config, config.llm_url)
    question_model = make_client(config, config.llm_question_url, question=True) if config.llm_question_url else None
    chosen = [s for s in SCENARIOS if not filters or any(f.casefold() in s.name for f in filters)]
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(scenario, run):
        async with gate:
            problem, seconds, helper = await loop_scenarios.run_one(scenario, config, prompt, model, question_model)
            return scenario, run, problem, seconds, helper

    started = time.perf_counter()
    results = await asyncio.gather(*(one(s, r) for s in chosen for r in range(RUNS)))
    failures = 0
    for scenario in chosen:
        rows = [r for r in results if r[0] is scenario]
        passed = sum(r[2] is None for r in rows)
        failures += passed < len(rows)
        times = [r[3] for r in rows]
        print(f"\n{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)}  {scenario.name}: "
              f"{scenario.utterance!r}  latency min/avg/max {min(times):.1f}/{sum(times)/len(times):.1f}/{max(times):.1f}s")
        for _, run, problem, seconds, helper in rows:
            print(f"   run {run + 1} {seconds:5.1f}s said={helper.spoken}")
            print(f"         highlights={helper.highlights()} actions={helper.actions}")
            if problem:
                print(f"         why: {problem}")
    print(f"\n{len(chosen) - failures}/{len(chosen)} scenarios passed every run, "
          f"{time.perf_counter() - started:.0f} s wall. Step log: {log}")
    await model.aclose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
