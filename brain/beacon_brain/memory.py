"""Bounded model context: the steps of one task, and the conversation around it."""

import json
import re
import time
from dataclasses import dataclass, field


@dataclass
class TaskMemory:
    prompt: str
    utterance: str
    previous: str = ""
    turns: list[dict] = field(default_factory=list)
    done_so_far: list[str] = field(default_factory=list)
    answers: list[tuple[str, str]] = field(default_factory=list)

    def answer(self, question: str, reply: str) -> None:
        """Record the user's reply to ask_user/confirm under the original request.

        Replacing the request with the bare reply ("the reply one") loses what it answers,
        and the small model asks again.
        """
        self.answers.append((question.strip(), reply.strip()))

    @property
    def latest_words(self) -> str:
        """What the user said most recently: the request, or their last answer."""
        return self.answers[-1][1] if self.answers else self.utterance

    @property
    def all_words(self) -> str:
        return " ".join([self.utterance, *(reply for _, reply in self.answers)])

    @property
    def request(self) -> str:
        lines = [self.utterance]
        lines += [f'USER ANSWERED "{question}": {reply}' for question, reply in self.answers]
        return "\n".join(lines)

    def messages(self, snapshot: dict) -> list[dict]:
        system = {"role": "system", "content": self.prompt}
        messages = [system]
        if self.previous:
            # Before the turns, so it stays in the cached prefix for every step of the task.
            messages.append({"role": "user", "content": f"EARLIER CONVERSATION:\n{self.previous}"})
        messages.extend(self.turns)
        content = f"SCREEN:\n{snapshot['text']}\n\nREQUEST: {self.request}"
        if self.done_so_far:
            # Without this the repeated REQUEST reads as new and the model acts again.
            content += (f"\nALREADY DONE: {'; '.join(self.done_so_far)}."
                        "\nIf the request is now complete, call done and say only what was done."
                        " Otherwise take the next step.")
        messages.append({"role": "user", "content": content})
        return messages

    def add(self, call, result: dict, snapshot_id: str, request: str, label: str = "") -> None:
        if result.get("ok") and call.name in {"click", "type_text", "set_value", "press_key", "scroll", "open_app"}:
            target = f" {label}" if label else ""
            detail = call.arguments.get("text") or call.arguments.get("keys") or call.arguments.get("name") or ""
            self.done_so_far.append(f"{call.name}{target}{f' {detail!r}' if detail else ''}")
        self.turns.extend([
            {
                "role": "user",
                "content": f"[snapshot {snapshot_id} omitted]\nREQUEST: {request}",
            },
            {"role": "assistant", "tool_calls": [{
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }]},
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)},
        ])

    @property
    def one_line(self) -> str:
        return self.request.replace("\n", " / ")


@dataclass(frozen=True)
class Exchange:
    said: str  # the user's request, already rewritten to stand alone if it was a follow-up
    reply: str  # what Beacon said, or what it did
    app: str = ""  # the app whose screen the exchange used; "" for a general question
    at: float = 0.0


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return match.group(1) if match else text


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0] + "…" if limit > 1 else ""


class Conversation:
    """The last few exchanges, faded to fit a fixed token budget.

    Rendered newest first until the budget runs out: the newest exchange keeps its reply
    (up to NEWEST_REPLY characters), older ones only the reply's first sentence, and the
    oldest are left out. The whole block never exceeds `budget_tokens`, however long the
    session runs. Everything is forgotten after `idle_s` of silence or on "forget that".
    """

    NEWEST_REPLY = 320
    OLDER_REPLY = 120
    REQUEST = 160

    def __init__(self, budget_tokens: int = 240, max_turns: int = 4, idle_s: float = 300,
                 clock=time.monotonic):
        self.budget_tokens = budget_tokens
        self.max_turns = max_turns
        self.idle_s = idle_s
        self.clock = clock
        self._exchanges: list[Exchange] = []

    def remember(self, said: str, reply: str, app: str = "") -> None:
        said, reply = " ".join(said.split()), " ".join(reply.split())
        if not said or not reply or self.budget_tokens <= 0:
            return
        self._exchanges = [*self.exchanges, Exchange(said, reply, app, self.clock())][-self.max_turns:]

    def forget(self) -> None:
        self._exchanges = []

    @property
    def exchanges(self) -> list[Exchange]:
        if self._exchanges and self.clock() - self._exchanges[-1].at > self.idle_s:
            self._exchanges = []
        return self._exchanges

    @property
    def last(self) -> Exchange | None:
        exchanges = self.exchanges
        return exchanges[-1] if exchanges else None

    def text(self) -> str:
        """The exchanges that fit the budget, oldest first, as "User: … / Beacon: …" lines."""
        blocks: list[str] = []
        used = 0
        for age, exchange in enumerate(reversed(self.exchanges)):
            said = f"User: {_clip(exchange.said, self.REQUEST)}\nBeacon: "
            if age == 0:  # the newest always fits: its reply shrinks to the room left
                room = min(self.NEWEST_REPLY, self.budget_tokens * 4 - len(said))
                block = said + _clip(exchange.reply, max(room, 0))
            else:
                block = said + _clip(_first_sentence(exchange.reply), self.OLDER_REPLY)
            cost = (len(block) + 3) // 4
            if used + cost > self.budget_tokens:
                break
            blocks.append(block)
            used += cost
        return "\n".join(reversed(blocks))
