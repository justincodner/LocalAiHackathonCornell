"""Per-step JSON Lines logging for local runs."""

import json
import time
from pathlib import Path


def append_step(path: Path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": time.time_ns() // 1_000_000, **fields}, ensure_ascii=False) + "\n")
