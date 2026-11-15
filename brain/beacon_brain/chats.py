"""Switch to a named chat ("switch to the poker chat") and send to it, from any app, in code.

The model can't do this: Messages shows its whole conversation list as one text element, so
there is nothing to click, and in another app the chat isn't on screen at all. Each chat app
has a keyboard route instead:

- Messages: Cmd+F, type the name, wait for the "Conversations" results, arrow down to the one
  that matches, Return opens it, Escape puts the cursor in the message field. The first
  Return after a fresh search is sometimes ignored, so the name is retyped and tried again.
- Discord and Slack (app or browser tab): Cmd+K opens the quick switcher; type the name,
  Return.

Nothing is typed into a chat until the window shows the named conversation, so a near miss
stops with "I couldn't find ..." instead of messaging the wrong person.
"""

import asyncio
import difflib
import re
from dataclasses import dataclass

from . import compose
from .browser import LEAD
from .page_summary import BROWSERS

I = re.IGNORECASE
LEAD_ANY_CASE = re.compile(LEAD.pattern, I)
CHAT_WORD = r"(?:group\s+chat|group\s+text|group|chat|conversation|convo|thread|dms?|channel|messages|texts)"
IN_APP = re.compile(r"\s+(?:in|on|over|using|via)\s+(?:the\s+)?(?P<app>i?messages?|imessage|discord|slack)(?:\s+app)?$", I)
TARGET = rf"(?:the\s+|my\s+|our\s+)?(?P<name>[\w&'.,@ -]+?)(?:\s+{CHAT_WORD})"
TO_PLACE = r"(?:switch|change|go|move|jump|head|flip|take\s+me|bring\s+me|get\s+me)\s+(?:over\s+|back\s+)*(?:to|into)"
SWITCH = [
    re.compile(rf"^{TO_PLACE}\s+{TARGET}$", I),
    re.compile(rf"^(?:open|pull\s+up|bring\s+up|show(?:\s+me)?)\s+{TARGET}$", I),
    re.compile(rf"^(?:open|{TO_PLACE}|pull\s+up|show(?:\s+me)?)\s+(?:the\s+|my\s+)?{CHAT_WORD}\s+with\s+"
               rf"(?P<name>[\w&'.@ -]+?)$", I),
]
# The same request with the app named instead of "chat": "switch to poker in messages".
SWITCH_IN_APP = re.compile(rf"^{TO_PLACE}\s+(?:the\s+|my\s+)?(?P<name>[\w&'.@ -]+?)$", I)
SEND = re.compile(r"^(?:send|text|message|tell|write|dm)\b", I)
SEND_TO_TARGET = re.compile(rf"^(?:send|write|post)\s+(?P<body>.+?)\s+(?:to|in|into)\s+{TARGET}$", I)
SEND_MESSAGE_TO = re.compile(rf"^(?:send|write|post)\s+(?:a\s+|an\s+)?(?:quick\s+)?(?:message|text|note|imessage)\s+"
                             rf"(?:to|in|into)\s+{TARGET}(?:\s*[,:]?\s*(?:saying|that\s+says|to\s+say|that)?\s*"
                             rf"(?P<after>.+))?$", I)
TELL_TARGET = re.compile(rf"^(?:text|message|tell)\s+{TARGET}\s*[,:]?\s*(?:that\s+|saying\s+)?(?P<body>.+)?$", I)
NOT_NAMES = {"next", "previous", "last", "other", "new", "a", "this", "that", "same", "first", "group", "it"}


@dataclass
class Request:
    name: str            # the chat the user named ("poker")
    app: str | None      # "Messages", "Discord", "Slack", or None to decide from the screen
    message: str | None  # what to send, or None to only switch (or to ask)
    send: bool


def parse(utterance: str) -> Request | None:
    """The chat to switch to (and the message to send there), or None when the request isn't one."""
    text = " ".join(re.sub(r"[\"“”]", "", utterance).split()).rstrip(" .")
    text = re.sub(r"(?i)^(?:(?:hey\s+)?beacon|ok(?:ay)?|so|um)\s*[,.]\s*", "", text)
    text = LEAD_ANY_CASE.sub("", text)
    text = re.sub(r"(?i)[\s,]+please[.!?]*$", "", text).strip()
    app = None
    in_app = IN_APP.search(text)
    if in_app:
        word = in_app.group("app").casefold()
        app = "Messages" if word.startswith(("message", "imessage")) else word.title()
        text = text[:in_app.start()].strip()
    if SEND.match(text):
        for pattern in (SEND_MESSAGE_TO, SEND_TO_TARGET, TELL_TARGET):
            match = pattern.match(text)
            if match:
                body = (match.groupdict().get("body") or match.groupdict().get("after") or "").strip(" ,.:")
                return _request(match.group("name"), app, body or None, send=True)
        if app:
            # "Send hello to Sam on Messages", "text Sam hi in discord": the app is named instead.
            target = re.match(r"^(?:send|write)\s+(?P<body>.+?)\s+to\s+(?P<name>[\w&'.@-]+(?:\s+[\w&'.@-]+)?)$", text, I) \
                or re.match(r"^(?:text|message|tell|dm)\s+(?P<name>[\w&'.@-]+)\s*[,:]?\s*(?:that\s+)?(?P<body>.*)$", text, I)
            if target:
                return _request(target.group("name"), app, target.group("body").strip(" ,.:") or None, send=True)
        return None
    for pattern in SWITCH:
        match = pattern.match(text)
        if match:
            return _request(match.group("name"), app, None, send=False)
    if app:
        match = SWITCH_IN_APP.match(text)
        if match:
            return _request(match.group("name"), app, None, send=False)
    return None


def _request(name: str | None, app: str | None, message: str | None, *, send: bool) -> Request | None:
    name = re.sub(r"(?i)^(?:the|my|our)\s+|\s+(?:group|chat)$", "", (name or "").strip(" ,.:?!")).strip()
    if not name or name.casefold() in NOT_NAMES or len(name.split()) > 4:
        return None
    if message and message.casefold() in compose.NOT_CONTENT:
        message = None
    return Request(name, app, message, send)


def chat_app(request: Request, front: dict) -> str:
    """Which app the chat is in: the one named, else the chat app in front, else Messages."""
    if request.app:
        return request.app
    app = (front.get("app") or "").casefold()
    window = (front.get("window") or "").casefold()
    if app in {"discord", "slack", "messages"}:
        return front["app"]
    if app in BROWSERS and ("discord" in window or "slack" in window):
        return "Discord" if "discord" in window else "Slack"
    return "Messages"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def name_score(name: str, label: str) -> float:
    """How well a conversation's label matches the spoken name: 1 exact, 0.9 whole words, else fuzzy."""
    spoken, shown = " ".join(_words(name)), " ".join(_words(label))
    if not spoken or not shown:
        return 0.0
    if spoken == shown:
        return 1.0
    if re.search(rf"\b{re.escape(spoken)}\b", shown):
        return 0.9 - 0.01 * (len(shown) - len(spoken)) / max(len(shown), 1)
    return difflib.SequenceMatcher(None, spoken, shown).ratio() * 0.85


def best_match(name: str, labels: list[str]) -> int | None:
    scored = [(name_score(name, label), -index) for index, label in enumerate(labels)]
    if not scored:
        return None
    score, index = max(scored)
    return -index if score >= 0.7 else None


def window_shows(name: str, snapshot: dict) -> bool:
    window = (snapshot.get("window") or "").split(" - Google Chrome")[0]
    return name_score(name, window) >= 0.75 or compose.chat_open(name, snapshot)


def results(snapshot: dict) -> list[dict]:
    """The "Conversations" buttons in Messages' search results, in order."""
    elements = snapshot.get("elements", [])
    start = next((i for i, item in enumerate(elements) if item.get("role") == "text"
                  and (item.get("label") or "").strip() == "Conversations"), None)
    if start is None:
        return []
    found = []
    for item in elements[start + 1:]:
        if item.get("role") != "button":
            break
        found.append(item)
    return found


class Switcher:
    """Runs the keyboard route for one chat app through the Helper."""

    POLL_S = 0.2

    def __init__(self, helper, settle_s: float = 0.3):
        self.helper = helper
        self.settle_s = settle_s

    async def key(self, keys: str) -> None:
        await self.helper.request("/input/key", {"keys": keys})

    async def type(self, text: str) -> None:
        await self.helper.request("/input/type", {"text": text})

    async def wait(self, seconds: float) -> None:
        if seconds > 0 and self.settle_s > 0:  # settle 0 (tests): never sleep
            await asyncio.sleep(seconds)

    async def messages(self, name: str) -> tuple[dict | None, str | None]:
        """Open the named Messages conversation. (snapshot showing it, its name), or (None, why)."""
        await self.helper.request("/app/open", {"name": "Messages"})
        await self.wait(self.settle_s)
        snapshot = await self.helper.snapshot()
        if window_shows(name, snapshot) and compose.message_field(snapshot):
            return snapshot, snapshot.get("window") or name
        await self.key("cmd+f")
        for attempt in range(3):
            await self.key("cmd+a")
            await self.type(name)
            found, previous = [], None
            for _ in range(int(4 / self.POLL_S)):
                await self.wait(self.POLL_S)
                snapshot = await self.helper.snapshot()
                labels = [item.get("label") or "" for item in results(snapshot)]
                if labels and labels == previous and best_match(name, labels) is not None:
                    found = labels
                    break
                previous = labels
            index = best_match(name, found)
            if index is None:
                await self.key("escape")
                return None, f"I couldn't find a Messages chat called {name}."
            await self.wait(self.settle_s)
            for _ in range(index + 1):
                await self.key("down")
                await self.wait(0.15)
            await self.wait(self.settle_s)
            await self.key("return")
            for _ in range(8):
                await self.wait(0.25)
                snapshot = await self.helper.snapshot()
                if window_shows(found[index], snapshot) or window_shows(name, snapshot):
                    await self.key("escape")  # leave search: the cursor goes to the message field
                    await self.wait(self.settle_s)
                    return await self.helper.snapshot(), found[index]
        await self.key("escape")
        return None, f"I found {name} in Messages but couldn't open the chat."

    async def quick_switcher(self, app: str, name: str, front: dict) -> tuple[dict | None, str | None]:
        """Discord or Slack: bring it forward (its browser tab, or the app), then Cmd+K, name, Return."""
        snapshot = await self.helper.snapshot()
        in_front = snapshot["app"].casefold() == app.casefold() or (
            snapshot["app"].casefold() in BROWSERS and app.casefold() in (snapshot.get("window") or "").casefold())
        if not in_front:
            tab = next((item for item in snapshot.get("elements", []) if item.get("role") == "tab"
                        and app.casefold() in (item.get("label") or "").casefold()), None)
            if tab is not None and snapshot["app"].casefold() in BROWSERS:
                await self.helper.request("/ax/act", {"snapshot_id": snapshot["snapshot_id"], "element_id": tab["id"],
                                                      "action": "press"})
            else:
                try:
                    await self.helper.request("/app/open", {"name": app})
                except Exception:  # noqa: BLE001 - not installed: maybe open in a browser tab elsewhere
                    return None, f"I couldn't find {app} open on this Mac."
            await self.wait(self.settle_s * 2)
            snapshot = await self.helper.snapshot()
        if window_shows(name, snapshot) and compose.message_field(snapshot):
            return snapshot, name
        await self.key("cmd+k")
        await self.wait(self.settle_s)
        await self.type(name)
        await self.wait(0.8)
        await self.key("return")
        for _ in range(8):
            await self.wait(0.25)
            snapshot = await self.helper.snapshot()
            if window_shows(name, snapshot):
                return snapshot, compose.shown_name(name, snapshot)
        return None, f"I couldn't open {name} in {app}."

    async def open(self, app: str, name: str, front: dict) -> tuple[dict | None, str | None]:
        if app == "Messages":
            return await self.messages(name)
        return await self.quick_switcher(app, name, front)


ELSEWHERE_APPS = {"messages", "discord", "slack", "mail", "microsoft outlook", "outlook", "whatsapp", "telegram"}


def person_send(utterance: str, snapshot: dict) -> Request | None:
    """ "Send hello to Emmett" in Notes or Finder, where there is nowhere to type it: Messages."""
    from . import fastpath

    app = (snapshot.get("app") or "").casefold()
    if (not fastpath.wants_send(utterance) or app in ELSEWHERE_APPS or app in BROWSERS
            or compose.message_field(snapshot) is not None):
        return None
    name = compose.recipient(utterance)
    message = compose.message_for(utterance, [])
    if not name or not message:
        return None
    return Request(name, "Messages", message, True)


def here_send(utterance: str, snapshot: dict) -> Request | None:
    """ "Send to this chat, did you find it?", "send hello" with a chat open: type it there and send.

    Only when the open chat is clearly the target: the user said here / this chat, named no one,
    or named the person whose chat is open. Mail is left to drafts.py.
    """
    from . import fastpath

    here = compose.here_content(utterance)
    if not (fastpath.wants_send(utterance) or here) or not compose.return_sends(snapshot):
        return None
    field = compose.message_field(snapshot)
    if field is None or (field.get("value") or "").strip():
        return None  # no chat box, or a draft is already there ("send it" sends that)
    message = here
    if message is None:
        message = compose.command_and_content(utterance)[1]
        bare = re.match(r"(?i)^(?:please\s+|can\s+you\s+)?send\s+(?P<body>.+?)[.!]?$", utterance.strip())
        if message is None and bare and not compose.recipient(utterance) and not re.search(
                r"(?i)\b(?:message|text|note|dm|reply)\b", bare.group("body")) and not re.match(r"(?i)(?:the|a|an|my|this|that|it|them|him|her|to)\b|"
                                                     r".*\bto\s+\w+$", bare.group("body")):
            message = bare.group("body")  # "send hello" with a chat open
        name = compose.recipient(utterance)
        if not message or (name and not compose.chat_open(name, snapshot, field)):
            return None
    window = re.sub(r"\s+-\s+(?:Google Chrome|Safari|Arc|Brave|Microsoft Edge|Firefox)$", "", snapshot.get("window") or "")
    window = re.sub(r"^(?:\(\d+\)\s*)?(?:Discord|Slack)\s*[|:-]\s*[@#]?", "", window).strip()
    return Request(window or "this chat", snapshot.get("app"), message, True)


BARE_SWITCH = re.compile(r"^(?:switch|change|go|move|jump|flip|head)\s+(?:over\s+|back\s+)*(?:to|into|the)\s+"
                         r"(?:the\s+|my\s+)?(?P<name>[\w&'.@ -]+?)"
                         r"(?:\s*,?\s+(?:and|then)\s+(?:then\s+)?(?:send|say|text|tell\s+(?:them|him|her)|ask|write)\s*"
                         r"[,:]?\s*(?P<body>.+))?$", I)


def bare_switch(utterance: str) -> Request | None:
    """ "Switch to Clav", "please switch the clav", "go to Clav and send how are you": a chat name with no
    "chat" word. Only a chat when a chat app is in front (the loop checks); None if it isn't this shape."""
    text = " ".join(re.sub(r"[\"“”]", "", utterance).split()).rstrip(" .!")
    text = re.sub(r"(?i)^(?:(?:hey\s+)?beacon|ok(?:ay)?|so|um)\s*[,.]\s*", "", text)
    text = re.sub(r"(?i)[\s,]+please$", "", LEAD_ANY_CASE.sub("", text)).strip()
    match = BARE_SWITCH.match(text)
    if not match:
        return None
    body = (match.group("body") or "").strip(" ,.") or None
    name = match.group("name").casefold()
    if (re.search(r"\b(?:tab|tabs|page|window|top|bottom|site|website|link|inbox|settings|home|start|end|next|"
                  r"previous|last|first|line|bar|field|menu|folder|messages?|imessage|discord|slack|app)\b", name)
            or (body and re.match(r"(?i)(?:a|an|the)\s+(?:message|text|dm)\b|to\s+", body))):
        return None  # a place on screen or an app, or "send a message to Sam" after it: not a chat name
    return _request(re.sub(r"(?i)\s+(?:chat|conversation)$", "", match.group("name")), None, body, send=body is not None)


def in_chat_app(front: dict) -> bool:
    app = (front.get("app") or "").casefold()
    window = (front.get("window") or "").casefold()
    return app in {"messages", "discord", "slack"} or (app in BROWSERS and ("discord" in window or "slack" in window))


TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}\s*[AP]M\b", re.IGNORECASE)


def is_conversation_list(element: dict | None) -> bool:
    """Messages' whole chat list as one text element ("David, How are you?, 11:45 PM Kai, alr, 10:46 PM ...")."""
    return bool(element) and len(TIMESTAMP.findall(element.get("label") or "")) >= 2
