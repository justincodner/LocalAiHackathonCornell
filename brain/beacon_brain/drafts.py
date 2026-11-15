"""Mail replies and drafts, run in code: open the reply, type the message, press Send.

The 2B model asks "What should the reply say?" when the request already said it, types into
the inbox's Search field, or types "send it" into a finished draft. A reply or "send it" in a
mail composer is a fixed sequence, so it runs here without a model call.
"""

import asyncio
import re
import time

from . import compose, fastpath, tools
from .helper_client import HelperError
from .llm import ToolCall
from .log import append_step

NOT_BODY = re.compile(r"^(?:to|cc|bcc|from|subject|reply[- ]to|search)\b", re.IGNORECASE)
BODY_GROUP = re.compile(r"\b(?:message )?body\b", re.IGNORECASE)
REPLY_REQUEST = re.compile(r"\b(?:reply|respond)\b")
# "Send it", "send this", "send the email", "okay send it now": send what is already written.
SEND_EXISTING = re.compile(
    r"(?:(?:ok|okay|now|please|go ahead and|and)\s+)*send(?:\s+(?:it|this|that|"
    r"(?:the|my|this)\s+(?:email|e mail|mail|message|reply|draft)))?(?:\s+(?:now|please))*"
)
OPEN_WAIT_S = 3.0  # Mail takes a moment to open the reply window
POLL_S = 0.2


def is_mail(snapshot: dict) -> bool:
    return snapshot["app"].casefold() in compose.MULTILINE_APPS


def _enabled(item: dict) -> bool:
    return not {"disabled", "inferred"} & set(item.get("states") or [])


def button(snapshot: dict, *labels: str) -> dict | None:
    """The first enabled button with one of these labels (Mail shows Reply twice)."""
    return next((item for item in snapshot["elements"] if item["role"] == "button" and _enabled(item)
                 and fastpath.normalize(item.get("label") or "") in labels), None)


def body_field(snapshot: dict) -> dict | None:
    """The message body of an open composer: never To, Subject or Search.

    Mail's real compose body is a focused group "message body", not a text area.
    """
    fields = [item for item in snapshot["elements"] if _enabled(item) and (
        (item["role"] in {"textarea", "textfield"} and not NOT_BODY.search((item.get("label") or "").strip()))
        or (item["role"] in {"group", "webarea", "scrollarea"} and BODY_GROUP.search(item.get("label") or "")))]
    if not fields:
        return None
    areas = [item for item in fields if item["role"] != "textfield"] or fields
    return next((item for item in areas if item["id"] == snapshot.get("focused_id")), areas[0])


def has_text(field: dict, snapshot: dict) -> bool:
    """The body holds something: its value, or (Mail's body group) text inside it."""
    if (field.get("value") or "").strip():
        return True
    elements = snapshot["elements"]
    index = next(i for i, item in enumerate(elements) if item["id"] == field["id"])
    for child in elements[index + 1:]:
        if child.get("depth", 0) <= field.get("depth", 0):
            return False
        if (child.get("label") or child.get("value") or "").strip():
            return True
    return False


def composer(snapshot: dict) -> dict | None:
    """The body field when a composer with a Send button is open, else None."""
    return body_field(snapshot) if button(snapshot, "send") else None


def wants_reply(utterance: str) -> bool:
    return fastpath.wants_send(utterance) and bool(REPLY_REQUEST.search(fastpath.normalize(utterance)))


def wants_send_existing(utterance: str) -> bool:
    return bool(SEND_EXISTING.fullmatch(fastpath.normalize(utterance)))


def reply_opener(snapshot: dict) -> ToolCall | None:
    """Press Reply, or cmd+R in a mail app, to open a reply to the selected message."""
    reply = button(snapshot, "reply")
    if reply is not None:
        return ToolCall("reply-open", "click", {"id": reply["id"]})
    if is_mail(snapshot):
        return ToolCall("reply-open", "press_key", {"keys": "cmd+r"})
    return None


def sentence(text: str) -> str:
    text = text.strip()
    return text if text[-1:] in {".", "!", "?"} else text + "."


def sent_text(snapshot: dict, utterance: str) -> str:
    name = compose.recipient(utterance, snapshot)
    return f"Sent to {name}." if name else "Sent."


class DraftRun:
    """One reply or send-it request, against the Brain's Helper, speech and config."""

    def __init__(self, brain, text: str, stt_ms: int):
        self.brain = brain
        self.text = text
        self.stt_ms = stt_ms
        self.step = 0

    async def act(self, call: ToolCall, snapshot: dict) -> dict:
        self.step += 1
        started = time.perf_counter()
        try:
            result = await self.brain.perform(call, snapshot, None)
        except HelperError as failure:
            result = {"ok": False, "error": {"code": failure.code, "message": str(failure)}}
        append_step(self.brain.config.log_path, task_id=f"t_{self.brain.task_number}", step=self.step,
                    utterance=self.text, kind="draft", route="draft", model=None,
                    snapshot_id=snapshot["snapshot_id"], snapshot_tokens=0, tool=call.name, args=call.arguments,
                    valid=True, risk="allow", ok=bool(result.get("ok")),
                    ms={"stt": self.stt_ms, "snapshot": snapshot.get("build_ms", 0), "llm": 0,
                        "act": round((time.perf_counter() - started) * 1000)})
        return result

    async def settled(self) -> dict:
        await asyncio.sleep(self.brain.config.settle_ms / 1000)
        return await self.brain.helper.snapshot()

    async def finish(self, summary: str) -> bool:
        await self.brain.say(summary)
        self.brain.remember(self.text, summary, self.app)
        return True

    async def send(self, snapshot: dict) -> bool:
        send = button(snapshot, "send")
        if send is None:
            return await self.finish("I couldn't find the Send button. The draft is still open.")
        result = await self.act(ToolCall("draft-send", "click", {"id": send["id"]}), snapshot)
        if not result.get("ok"):
            return await self.finish("I couldn't press Send. The draft is still open.")
        return await self.finish(sent_text(snapshot, self.text))

    async def reply(self, snapshot: dict) -> bool:
        if composer(snapshot) is None:
            opener = reply_opener(snapshot)
            result = await self.act(opener, snapshot)
            if not result.get("ok"):
                return await self.finish("I couldn't open a reply. Select the email first, then try again.")
            snapshot = await self.wait_for_composer()
            if snapshot is None:
                return await self.finish("I couldn't find the reply window. Please try again.")
        field = composer(snapshot)
        content = compose.command_and_content(self.text)[1]
        if not content:
            answer = await self.brain.ask(compose.ASK_MESSAGE)
            if not answer or fastpath.classify(answer) in {"stop", "no"}:
                return await self.finish("Okay. The reply is still open.")
            content = answer.strip()
        # Type at the caret when the body already has focus (above Mail's quoted text).
        where = {} if field["id"] == snapshot.get("focused_id") else {"id": field["id"]}
        result = await self.act(ToolCall("draft-type", "type_text", {**where, "text": content}), snapshot)
        if not result.get("ok"):
            return await self.finish("I couldn't type the reply. It is still open.")
        snapshot = await self.settled()
        if self.brain.config.confirm_sends:
            name = compose.recipient(self.text, snapshot)
            draft = f"Reply to {name}: {sentence(content)}" if name else f"Reply: {sentence(content)}"
            answer = await self.brain.ask(f"{draft} Say yes to continue.", confirm=True)
            if fastpath.classify(answer or "") != "yes":
                return await self.finish("Okay, cancelled. The reply is still open." if answer
                                         else "The reply is still open.")
            snapshot = await self.brain.helper.snapshot()
        return await self.send(snapshot)

    async def wait_for_composer(self) -> dict | None:
        deadline = time.monotonic() + OPEN_WAIT_S
        snapshot = await self.settled()
        while composer(snapshot) is None:
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(POLL_S)
            snapshot = await self.brain.helper.snapshot()
        return snapshot


def flow_for(text: str, snapshot: dict) -> str | None:
    """Which draft flow handles this request: "send", "reply", "nothing" (no draft), or None."""
    if not fastpath.wants_send(text):
        return None
    field = composer(snapshot)
    if wants_send_existing(text):
        if field is not None and has_text(field, snapshot):
            return "send"
        if is_mail(snapshot) and body_field(snapshot) is None:
            return "nothing"
        return None
    if not wants_reply(text):
        return None
    if not compose.command_and_content(text)[1]:
        return None  # "Reply to her": the model has the conversation to work out what to say
    name = compose.recipient(text)
    if name and not compose.on_screen(name, snapshot):
        return None  # "Reply to David" while Sarah's email is open: let the model look
    if field is None and reply_opener(snapshot) is None:
        return None
    return "reply"


async def run(brain, text: str, snapshot: dict, stt_ms: int) -> bool:
    """Handle a reply or send-it request in code. False leaves it to the model loop."""
    flow = flow_for(text, snapshot)
    if flow is None:
        return False
    draft = DraftRun(brain, text, stt_ms)
    draft.app = snapshot.get("app", "")
    if flow == "nothing":
        last = brain.conversation.last  # "Reply that ...", then "Send it"
        sent = last is not None and last.reply.startswith("Sent")
        return await draft.finish("That's already sent." if sent else "There's no draft open to send.")
    if flow == "send":
        return await draft.send(snapshot)
    return await draft.reply(snapshot)
