"""Emmet / B1. Run with: brain/.venv/Scripts/python scripts/bench.py --model 2B

Measures what the voice loop actually pays per request: the fixed system prompt + tool
schemas are prewarmed into llama-server's prompt cache (as the Brain does at start-up),
then each request only processes the snapshot and utterance. A sweep over snapshot sizes
shows the largest snapshot that fits the 2.5 s budget (spec 6.1, 10.2).
"""

import argparse
import asyncio
import json
import platform
import re
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_brain.config import Config  # noqa: E402
from beacon_brain.llm import BonsaiClient, BonsaiError  # noqa: E402
from mock_helper.replay import render_screen  # noqa: E402

UTTERANCES = (
    "What is on my screen?",
    "Where is the submit button?",
    "Read the first heading aloud.",
)
BUDGET_S = 2.5


def default_fixture() -> Path:
    for path in (ROOT / "contracts" / "fixtures" / "mail_inbox.json",
                 ROOT / "contracts" / "fixtures" / "safari_form.json",
                 ROOT / "mock_helper" / "fixtures" / "mail_inbox.json"):
        if path.is_file():
            return path
    raise FileNotFoundError("No snapshot fixture found")


def cut(snapshot: dict, count: int | None) -> str:
    if count is None or count >= len(snapshot["elements"]):
        return snapshot["text"]
    return render_screen(dict(snapshot, elements=snapshot["elements"][:count]))


def cell(value, fmt="{}"):
    return fmt.format(value) if value is not None else "n/a"


async def benchmark(args: argparse.Namespace, config: Config) -> bool:
    snapshot = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    prompt = args.prompt.read_text(encoding="utf-8").strip()
    tools = json.loads((ROOT / "contracts" / "tools.json").read_text(encoding="utf-8"))
    async with BonsaiClient(
        args.url, timeout_s=args.timeout_s, temperature=config.temperature, top_p=config.top_p,
        top_k=config.top_k, presence_penalty=config.presence_penalty, max_tokens=config.max_tokens,
        cache_prompt=config.cache_prompt,
    ) as client:
        model_id = await client.discover_model()
        if not re.search(rf"(?<![A-Z0-9]){args.model}(?![A-Z0-9])", model_id, re.IGNORECASE):
            raise ValueError(f"Expected the {args.model} model, but the server reported {model_id!r}")
        cold = await client.prewarm(prompt, tools)
        warm = await client.prewarm(prompt, tools)
        print(f"### Bonsai {args.model} (`{model_id}`) on {platform.system()} {platform.machine()}, "
              f"{platform.processor() or 'unknown CPU'}")
        print()
        print(f"Fixture `{args.fixture.name}` ({len(snapshot['elements'])} elements). "
              f"System prompt + tools prefix: {cold:.2f} s cold, {warm:.2f} s once cached.")
        print()
        print("| Snapshot elements | Utterance | Tool | Prompt tokens | Cached | Prompt tok/s | Gen tok/s | Time | Budget |")
        print("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
        sizes = [int(size) for size in args.sizes.split(",")] + [None]
        succeeded = True
        runs = [(size, UTTERANCES[0]) for size in sizes] + [(None, utterance) for utterance in UTTERANCES[1:]]
        for size, utterance in runs:
            screen = cut(snapshot, size)
            label = size if size is not None and size < len(snapshot["elements"]) else f"all {len(snapshot['elements'])}"
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"SCREEN:\n{screen}\n\nREQUEST: {utterance}"},
            ]
            started = perf_counter()
            try:
                completion = await client.complete(messages, tools)
            except BonsaiError as exc:
                print(f"| {label} | {utterance} | error: {exc} | | | | | | |")
                succeeded = False
                continue
            elapsed = perf_counter() - started
            timings = completion.timings or {}
            print(
                f"| {label} | {utterance} | `{completion.tool_call.name}` | {cell(completion.prompt_tokens)} | "
                f"{cell(timings.get('cache_n'))} | {cell(timings.get('prompt_per_second'), '{:.0f}')} | "
                f"{cell(completion.generation_tokens_per_second, '{:.1f}')} | {elapsed:.2f} s | "
                f"{'ok' if elapsed <= BUDGET_S else 'over'} |"
            )
        return succeeded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, choices=("2B", "4B", "9B"))
    parser.add_argument("--url", help="Override the Brain's configured Bonsai server URL")
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument("--sizes", default="20,40,80", help="Comma-separated snapshot element counts to sweep")
    parser.add_argument("--prompt", type=Path, default=ROOT / "brain" / "prompts" / "system_v1.txt")
    args = parser.parse_args()
    try:
        config = Config.from_environment()
        args.url = args.url or config.llm_url
        args.fixture = args.fixture or default_fixture()
        return 0 if asyncio.run(benchmark(args, config)) else 1
    except (OSError, ValueError, BonsaiError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
