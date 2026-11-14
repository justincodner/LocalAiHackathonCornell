"""Environment settings for the Brain and its local services."""

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    helper_url: str = "http://127.0.0.1:8765"
    llm_url: str = "http://127.0.0.1:8080/v1"
    # Optional second, more accurate model for questions (route.py). Empty = one model for everything.
    llm_question_url: str = ""
    llm_question_timeout_s: float = 8  # one attempt, then the fast model answers instead
    token: str = ""
    max_steps: int = 8
    task_timeout_s: float = 45
    llm_timeout_s: float = 12
    settle_ms: int = 400
    reply_timeout_s: float = 15
    temperature: float = 0.2  # 0.7 (PrismML's chat default) scored lower and less consistently; docs/bench.md
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 1.5
    max_tokens: int = 300
    cache_prompt: bool = True
    # Prompt processing is the bottleneck (~170 tok/s for the 4B on the M1), so ask the
    # Helper for fewer elements than its 250 default; tune from docs/bench.md.
    snapshot_max_elements: int = 120
    prewarm: bool = True
    thinking_filler_ms: int = 1500
    prompt_path: Path = ROOT / "brain" / "prompts" / "system_v3.txt"
    log_path: Path = ROOT / "logs" / "session.jsonl"

    @classmethod
    def from_environment(cls) -> "Config":
        values = {}
        env_file = ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                assignment = line.split("#", 1)[0].strip()
                key, separator, value = assignment.partition("=")
                if separator and key.startswith("BEACON_"):
                    values[key] = value.strip().strip("\"'")

        def get(name: str, default: str) -> str:
            key = f"BEACON_{name}"
            return os.getenv(key, values.get(key, default))

        return cls(
            helper_url=get("HELPER_URL", cls.helper_url),
            llm_url=get("LLM_URL", cls.llm_url),
            llm_question_url=get("LLM_QUESTION_URL", cls.llm_question_url),
            llm_question_timeout_s=float(get("LLM_QUESTION_TIMEOUT_S", str(cls.llm_question_timeout_s))),
            token=get("TOKEN", cls.token),
            max_steps=int(get("MAX_STEPS", str(cls.max_steps))),
            task_timeout_s=float(get("TASK_TIMEOUT_S", str(cls.task_timeout_s))),
            llm_timeout_s=float(get("LLM_TIMEOUT_S", str(cls.llm_timeout_s))),
            settle_ms=int(get("SETTLE_MS", str(cls.settle_ms))),
            reply_timeout_s=float(get("REPLY_TIMEOUT_S", str(cls.reply_timeout_s))),
            temperature=float(get("TEMPERATURE", str(cls.temperature))),
            top_p=float(get("TOP_P", str(cls.top_p))),
            top_k=int(get("TOP_K", str(cls.top_k))),
            presence_penalty=float(get("PRESENCE_PENALTY", str(cls.presence_penalty))),
            max_tokens=int(get("MAX_TOKENS", str(cls.max_tokens))),
            cache_prompt=get("CACHE_PROMPT", "1").lower() in {"1", "true", "yes"},
            snapshot_max_elements=int(get("SNAPSHOT_MAX_ELEMENTS", str(cls.snapshot_max_elements))),
            prewarm=get("PREWARM", "1").lower() in {"1", "true", "yes"},
            thinking_filler_ms=int(get("THINKING_FILLER_MS", str(cls.thinking_filler_ms))),
            prompt_path=ROOT / get("PROMPT_PATH", str(cls.prompt_path)),  # relative paths are repo-relative
            log_path=Path(get("LOG_PATH", str(cls.log_path))),
        )
