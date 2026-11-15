"""Start or stop the optional Bonsai 4B question model while Beacon runs (the HUD's ⋯ menu toggle)."""

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable

import httpx

from .config import ROOT, Config
from .llm import BonsaiError

LOGGER = logging.getLogger(__name__)

PORT = 8081
URL = f"http://127.0.0.1:{PORT}/v1"
PID_FILE = ROOT / "logs" / f"bonsai-{PORT}.pid"
LOADING = "Loading the 4B model for questions. This can take a minute."


def memory_gb() -> int | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except (ValueError, OSError, AttributeError):
        return None


def enabled_message(gigabytes: int | None = None) -> str:
    """What Beacon says once the 4B is on: a speed warning only where both models do not fit (under 16 GB)."""
    gigabytes = memory_gb() if gigabytes is None else gigabytes
    if gigabytes is not None and gigabytes < 16:
        return f"Using the 4B model for questions. It's slower on this {gigabytes} GB Mac."
    return "Using the 4B model for questions."
DISABLED = "Back to the 2B model for everything."


async def reachable(url: str = URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            return (await client.get(f"{url}/models")).status_code == 200
    except httpx.HTTPError:
        return False


async def launch_4b() -> None:
    """Run scripts/start_bonsai.sh for the 4B on PORT; it returns once the server answers (or fails)."""
    env = dict(os.environ, BONSAI_MODEL="4B", BONSAI_PORT=str(PORT),
               BONSAI_CTX=os.environ.get("BEACON_QUESTION_CTX", "8192"))
    process = await asyncio.create_subprocess_exec(
        "bash", str(ROOT / "scripts" / "start_bonsai.sh"), "--detach", env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise BonsaiError(f"the 4B did not start: {output.decode(errors='replace').strip()[-300:]}")


def stop_4b() -> None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    PID_FILE.unlink(missing_ok=True)


class QuestionModelSwitch:
    def __init__(self, config: Config, make_client: Callable, *,
                 is_up: Callable[[], Awaitable[bool]] = reachable,
                 launch: Callable[[], Awaitable[None]] = launch_4b, stop: Callable[[], None] = stop_4b):
        self.config = config
        self.make_client = make_client
        self.is_up = is_up
        self.launch = launch
        self.kill = stop
        self.started = False  # this Brain started the 4B

    async def start(self):
        if not await self.is_up():
            await self.launch()
            self.started = True
        client = self.make_client(self.config, URL, question=True)
        try:
            LOGGER.info("Bonsai model %s (questions)", await client.discover_model())
        except BonsaiError:
            await client.aclose()
            await self.stop(None)
            raise
        return client

    async def stop(self, client) -> None:
        """Close the client and stop the 4B server if a Beacon script started it (it has a pid file), freeing
        its memory. A 4B started some other way is left running."""
        if client is not None:
            await client.aclose()
        self.kill()
        self.started = False
