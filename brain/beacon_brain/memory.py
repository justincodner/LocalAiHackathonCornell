"""Bounded model context for a single task and its immediate predecessor."""

import json
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
            messages.append({"role": "user", "content": self.previous})
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

    def summarize(self, response: str) -> str:
        request = self.request.replace("\n", " / ")
        return f"Previous request: {request[:300]}\nPrevious result: {response[:500]}"
