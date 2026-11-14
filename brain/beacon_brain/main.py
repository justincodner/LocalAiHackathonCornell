"""Connect the local model to the Helper and dispatch its voice events."""

import asyncio
import logging

import httpx
from websockets.exceptions import WebSocketException

from .config import ROOT, Config
from .helper_client import HelperClient, HelperError
from .llm import BonsaiClient, BonsaiError
from .loop import Brain
from .tools import TOOL_DEFINITIONS


LOGGER = logging.getLogger(__name__)


async def run(config: Config) -> None:
    expected_version = int((ROOT / "contracts" / "VERSION").read_text(encoding="utf-8").strip())
    prompt = config.prompt_path.read_text(encoding="utf-8").strip()
    helper = HelperClient(config.helper_url, config.token, max_elements=config.snapshot_max_elements)
    model = BonsaiClient(
        config.llm_url, config.llm_timeout_s, temperature=config.temperature, top_p=config.top_p,
        top_k=config.top_k, presence_penalty=config.presence_penalty, max_tokens=config.max_tokens,
        cache_prompt=config.cache_prompt,
    )
    brain = Brain(helper, model, config, prompt)
    try:
        model_name = await model.discover_model()
        LOGGER.info("Bonsai model %s", model_name)
        if config.prewarm:
            try:
                seconds = await model.prewarm(prompt, TOOL_DEFINITIONS)
                LOGGER.info("Prompt cache warmed in %.1f s", seconds)
            except BonsaiError as error:  # an optimisation only; the first request will be slow
                LOGGER.warning("Prompt cache not warmed: %s", error)
        while True:
            try:
                health = await helper.health()
                version = health.get("contract_version")
                if version != expected_version:
                    raise RuntimeError(f"Helper contract version {version!r} differs from {expected_version}")
                LOGGER.info("Helper contract v%s; listening for voice events", version)
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
