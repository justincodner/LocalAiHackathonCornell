"""Find things on the screen without a model call.

where():      "Where do I pay?" -> the matching control, its coarse position and label note.
read_aloud(): reading a mail list row reads the message itself (selecting the row if needed),
              with the highlight ring on what is being read.
"""

import asyncio
import re

from . import fastpath, page_summary, tools
from .llm import ToolCall

NEAR = re.compile(r"unlabeled, near '(.+)'")
CONTROL_ROLES = {"button", "link", "menuitem", "popup", "checkbox", "radio", "textfield", "textarea",
                 "combobox", "tab", "slider"}
ROLE_NAMES = {"textfield": "field", "textarea": "text box", "popup": "menu", "combobox": "menu",
              "menuitem": "menu item", "radio": "option"}
SYNONYMS = [
    {"pay", "payment", "checkout", "check out", "buy", "purchase", "place order", "order", "submit",
     "complete purchase", "total"},
    {"sign in", "log in", "login", "signin"},
    {"sign up", "register", "create account"},
    {"search", "find"},
    {"send", "post", "submit"},
]
FILLER = {"the", "a", "an", "i", "to", "my", "on", "this", "page", "screen", "button", "link", "field", "box",
          "option", "menu", "tab", "can", "do", "should", "here", "for", "it", "please", "go", "click", "press",
          "tap", "find", "is", "are", "of", "in", "me", "you", "what", "where", "how", "now", "right"}
TARGET = re.compile(
    r"^(?:please )?(?:where (?:do|can|should|would) i (?:go to |click to |press to |tap to )?"
    r"|how (?:do|can|should) i "
    r"|where(?:s| is| are) (?:the |a |an )?"
    r"|(?:find|show me) (?:the |a |an )?)(?P<target>.+)$"
)
MAIL_APPS = {"mail", "microsoft outlook", "outlook", "spark", "airmail", "thunderbird", "mimestream"}
MIN_BODY_CHARS = 25


def _words(text: str) -> str:
    return f" {fastpath.normalize(text)} "


def target_terms(request: str) -> set[str]:
    """The phrases a matching control's label may contain: the asked words, widened by synonyms."""
    match = TARGET.match(fastpath.normalize(request))
    if not match:
        return set()
    words = [word for word in match.group("target").split() if word not in FILLER]
    if not words:
        return set()
    phrase = " ".join(words)
    for group in SYNONYMS:
        if any(f" {term} " in f" {phrase} " for term in group):
            return set(group)
    return {phrase}


def _score(element: dict, terms: set[str]) -> int:
    label = element.get("label") or ""
    near = NEAR.fullmatch(label)
    text = _words(near.group(1) if near else label)
    if not any(f" {term} " in text for term in terms):
        return 0
    return 1 if near else 2  # a real label beats a guess from nearby text


def position(element: dict, elements: list[dict]) -> str:
    """Coarse spoken position of the element inside the area the elements cover."""
    boxes = [item["bounds"] for item in elements if len(item.get("bounds") or []) == 4]
    if not boxes or len(element.get("bounds") or []) != 4:
        return ""
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    width = max(b[0] + b[2] for b in boxes) - left or 1
    height = max(b[1] + b[3] for b in boxes) - top or 1
    x, y, w, h = element["bounds"]
    column = min(2, int(3 * (x + w / 2 - left) / width))
    row = min(2, int(3 * (y + h / 2 - top) / height))
    vertical, horizontal = ("Top", "Middle", "Bottom")[row], ("left", "center", "right")[column]
    if (vertical, horizontal) == ("Middle", "center"):
        return "In the middle"
    if vertical == "Middle":
        return f"On the {horizontal}"
    if horizontal == "center":
        return f"At the {vertical.lower()}"
    return f"{vertical} {horizontal}"


def where(snapshot: dict, request: str) -> tuple[str, int] | None:
    """(answer, element id) for "where is X / where do I X", or None to answer another way."""
    terms = target_terms(request)
    if not terms:
        return None
    browser = snapshot["app"].casefold() in page_summary.BROWSERS
    elements = page_summary.page_elements(snapshot) if browser else snapshot["elements"]
    scored = [(_score(item, terms), item) for item in elements
              if item["role"] in CONTROL_ROLES and "disabled" not in item.get("states", [])
              and (item.get("label") or "").casefold() not in page_summary.WINDOW_BUTTONS]
    best = max((score for score, _ in scored), default=0)
    matches = [item for score, item in scored if score == best and best]
    if len({item.get("label") for item in matches}) != 1:
        return None  # nothing, or several different controls: let the usual answer handle it
    element = matches[0]
    role = ROLE_NAMES.get(element["role"], element["role"])
    near = NEAR.fullmatch(element.get("label") or "")
    if near:
        note = f"The {role} has no label; it is next to {near.group(1).rstrip('.')}."
    else:
        note = f"It is the {element['label']} {role}."
    spot = position(element, elements)
    return (f"{spot}. {note}" if spot else note), element["id"]


def message_body(snapshot: dict) -> dict | None:
    """The open message's text in a mail window: the longest text outside the message list."""
    title = fastpath.normalize(snapshot["window"])
    in_list_depth = None
    best = None
    for item in snapshot["elements"]:
        depth = item.get("depth", 0)
        if in_list_depth is not None and depth > in_list_depth:
            continue
        in_list_depth = depth if item["role"] in {"list", "table", "outline", "toolbar"} else None
        label = item.get("label") or ""
        if (item["role"] == "text" and len(label) >= MIN_BODY_CHARS and fastpath.normalize(label) != title
                and (best is None or len(label) > len(best["label"]))):
            best = item
    return best


def _same(element: dict, snapshot: dict) -> dict | None:
    return next((item for item in snapshot["elements"]
                 if (item["role"], item.get("label")) == (element["role"], element.get("label"))), None)


async def read_aloud(brain, snapshot: dict, element_id: int) -> str:
    """Read an element aloud with the ring on it. A mail list row reads the message it opens."""
    helper = brain.helper
    element = tools.element_for(ToolCall("read", "read_element", {"id": element_id}), snapshot) or {}
    ring, read_from, read_id = element_id, snapshot, element_id
    if element.get("role") == "row" and snapshot["app"].casefold() in MAIL_APPS:
        screen = snapshot
        if "selected" not in element.get("states", []):
            await tools.execute(helper, ToolCall("read-select", "click", {"id": element_id}), snapshot, None)
            await asyncio.sleep(brain.config.settle_ms / 1000)
            screen = await helper.snapshot()
        body = message_body(screen)
        row = _same(element, screen)
        if body is not None and (row is None or "selected" in row.get("states", [])):
            read_from, read_id = screen, body["id"]
            ring = row["id"] if row else body["id"]
    text = await helper.read(read_from["snapshot_id"], read_id)
    await brain.set_state("speaking", caption=text, highlight=tools.element_ref(read_from, ring))
    await brain.speak(text)
    return text
