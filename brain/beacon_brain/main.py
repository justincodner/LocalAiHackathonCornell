"""Connect the local model to the Helper and dispatch its voice events."""

import asyncio
import logging

import httpx
from websockets.exceptions import WebSocketException

from .config import ROOT, Config
from .helper_client import HelperClient, HelperError
from .llm import BonsaiClient, BonsaiError
from .loop import Brain, prompt_families
from .question_switch import QuestionModelSwitch


LOGGER = logging.getLogger(__name__)


def make_client(config: Config, url: str, *, question: bool = False) -> BonsaiClient:
    """The fast model retries once (spec 8.5). The optional question model gets one short attempt,
    so a busy or hung 4B costs at most its timeout before the fast model answers instead."""
    return BonsaiClient(
        url, config.llm_question_timeout_s if question else config.llm_timeout_s,
        temperature=config.temperature, top_p=config.top_p,
        top_k=config.top_k, presence_penalty=config.presence_penalty, max_tokens=config.max_tokens,
        cache_prompt=config.cache_prompt, attempts=1 if question else 2, slots=config.llm_slots,
    )


async def prewarm(config: Config, model: BonsaiClient, prompt: str) -> None:
    """Warm every prompt family (loop.prompt_families), so none re-reads its fixed prefix."""
    if not config.prewarm:
        return
    total = 0.0
    families = prompt_families(prompt)
    # Each warm family keeps a slot of its own; warming more families than slots would only evict them.
    families = families[:1] if model.slot_count <= 1 else families[:len(model.slot_ids)]
    for system, family_tools in families:
        try:
            total += await model.prewarm(system, family_tools)
        except BonsaiError as error:  # an optimisation only; that family's requests will be slow
            LOGGER.warning("Prompt cache not warmed for %r: %s", system[:40], error)
    LOGGER.info("Prompt cache warmed in %.1f s", total)


async def run(config: Config) -> None:
    expected_version = int((ROOT / "contracts" / "VERSION").read_text(encoding="utf-8").strip())
    prompt = config.prompt_path.read_text(encoding="utf-8").strip()
    helper = HelperClient(config.helper_url, config.token, max_elements=config.snapshot_max_elements)
    model = make_client(config, config.llm_url)
    question_model = make_client(config, config.llm_question_url, question=True) if config.llm_question_url else None
    brain = None
    try:
        model_name = await model.discover_model()
        LOGGER.info("Bonsai model %s (actions)", model_name)
        await prewarm(config, model, prompt)
        if question_model is not None:
            try:
                LOGGER.info("Bonsai model %s (questions)", await question_model.discover_model())
                await prewarm(config, question_model, prompt)
            except BonsaiError as error:  # optional: run on the fast model alone
                LOGGER.warning("Question model unavailable (%s); using one model for everything", error)
                await question_model.aclose()
                question_model = None
        brain = Brain(helper, model, config, prompt, question_model)
        brain.question_switch = QuestionModelSwitch(config, make_client)
        while True:
            try:
                health = await helper.health()
                version = health.get("contract_version")
                if version != expected_version:
                    raise RuntimeError(f"Helper contract version {version!r} differs from {expected_version}")
                LOGGER.info("Helper contract v%s; listening for voice events", version)
                await brain.set_state("idle", question_model=brain.question_model is not None)  # the ⋯ menu's check
                async for event in helper.events():
                    await brain.on_event(event)
                LOGGER.warning("Helper event stream disconnected")
            except (HelperError, httpx.HTTPError, OSError, WebSocketException, TimeoutError, ValueError) as error:
                LOGGER.warning("Helper unavailable: %s", error)
            finally:
                await brain.cancel_active()
            await asyncio.sleep(1)
    finally:
        await helper.aclose()
        await model.aclose()
        if brain is not None and brain.question_switch is not None:
            await brain.question_switch.stop(brain.question_model)  # stops a 4B this Brain started
        elif question_model is not None:
            await question_model.aclose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        asyncio.run(run(Config.from_environment()))
    except KeyboardInterrupt:
        return 0
    except (BonsaiError, RuntimeError, ValueError, OSError) as error:
        LOGGER.error("Could not start Beacon: %s", error)
        return 1
    return 0
