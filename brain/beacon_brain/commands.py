"""One-step commands ("press enter", "next tab", "go back", "scroll down") run in code.

The model takes 3 to 18 seconds to turn "press enter" into press_key(return), and the loop
reads the whole window first. These requests name a key or a shortcut every Mac app shares,
so a short grammar turns them into the step straight away: the Brain only needs the
frontmost app's name (a one-element snapshot, ~10 ms) for the risk check and the few
shortcuts that differ between apps. Anything the grammar does not match whole, such as
"press call" or "go back to the inbox", is left to the model.
"""

import re

from .browser import Plan, _clean
from .llm import ToolCall
from .page_summary import BROWSERS

# Spoken key names -> Helper key names (helper/BeaconHelper/Input/KeyChord.swift).
KEYS = {
    "enter": "return", "return": "return", "the enter": "return", "the return": "return",
    "escape": "escape", "esc": "escape", "tab": "tab", "space": "space", "spacebar": "space", "space bar": "space",
    "delete": "delete", "backspace": "delete", "back space": "delete", "forward delete": "forwarddelete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "up arrow": "up", "down arrow": "down", "left arrow": "left", "right arrow": "right",
    "arrow up": "up", "arrow down": "down", "arrow left": "left", "arrow right": "right",
    "home": "home", "end": "end", "page up": "pageup", "page down": "pagedown",
    **{f"f{n}": f"f{n}" for n in range(1, 13)},
}
KEY_NAMES = {"return": "Enter", "escape": "Escape", "tab": "Tab", "space": "Space", "delete": "Delete",
             "forwarddelete": "Forward Delete", "pageup": "Page Up", "pagedown": "Page Down"}
MODIFIERS = {"command": "cmd", "cmd": "cmd", "shift": "shift", "option": "opt", "alt": "opt",
             "control": "ctrl", "ctrl": "ctrl", "function": "fn", "fn": "fn"}
SPOKEN_MODIFIERS = {"cmd": "Command", "shift": "Shift", "opt": "Option", "ctrl": "Control", "fn": "Function"}
COUNTS = {"once": 1, "twice": 2, "thrice": 3, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
          "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
MAX_REPEAT = 10

PRESS = re.compile(r"^(?:press|hit|tap|push|type|use)\s+(?:the\s+)?(?P<keys>.+?)(?:\s+(?:key|button))?$")
REPEAT = re.compile(r"\s+(?:(?P<word>once|twice|thrice)|(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
                    r"\s+times?)$")
SCROLL = re.compile(r"^scroll(?:\s+(?:the\s+)?(?:page|window|screen))?\s+(?P<dir>up|down|left|right)"
                    r"(?:\s+(?:a\s+(?:bit|little(?:\s+bit)?)|some|more|again|a\s+page|one\s+page))?$")
SCROLL_END = re.compile(r"^(?:scroll|go|jump|take\s+me)\s+(?:all\s+the\s+way\s+)?(?:up\s+|down\s+)?(?:to\s+)?"
                        r"(?:the\s+)?(?P<end>top|bottom)(?:\s+of\s+(?:the\s+)?(?:page|window|screen))?$")
PAGE = re.compile(r"^page\s+(?P<dir>up|down)$")

TAB = r"(?:tab|tabs)"
NEXT_TAB = re.compile(rf"^(?:(?:go|switch|move|change)\s+(?:to\s+)?(?:the\s+)?(?:next|other)\s+{TAB}"
                      rf"|(?:switch|change|swap|flip)\s+{TAB}|next\s+{TAB}|(?:the\s+)?{TAB}\s+(?:to\s+the\s+)?right)$")
PREVIOUS_TAB = re.compile(rf"^(?:(?:go|switch|move|change)\s+(?:back\s+)?(?:to\s+)?(?:the\s+)?(?:previous|prior)\s+{TAB}"
                          rf"|(?:go|switch|move)\s+back\s+(?:a|one)\s+{TAB}|previous\s+{TAB}"
                          rf"|(?:the\s+)?{TAB}\s+(?:to\s+the\s+)?left)$")
NUMBERED_TAB = re.compile(r"^(?:(?:go|switch|move|jump)\s+to\s+)?(?:the\s+)?(?:tab\s+(?:number\s+)?(?P<n>[1-8]|one|two|three|"
                          r"four|five|six|seven|eight)|(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|last)"
                          r"\s+tab)$")
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "last": 9}
BACK = re.compile(r"^(?:go\s+)?back(?:\s+(?:a|one)\s+page)?$|^(?:go\s+to\s+)?(?:the\s+)?previous\s+page$")
FORWARD = re.compile(r"^(?:go\s+)?forward(?:\s+(?:a|one)\s+page)?$")
RELOAD = re.compile(r"^(?:refresh|reload)(?:\s+(?:the|this)\s+(?:page|tab|site))?$")
REOPEN_TAB = re.compile(rf"^(?:reopen|restore|bring\s+back)\s+(?:the\s+)?(?:last\s+)?(?:closed\s+)?{TAB}$")

# Shortcuts shared by every Mac app: phrase -> (keys, caption, summary).
SHORTCUTS = {
    "undo": ("cmd+z", "Undoing", "Undone."), "undo that": ("cmd+z", "Undoing", "Undone."),
    "redo": ("cmd+shift+z", "Redoing", "Redone."), "redo that": ("cmd+shift+z", "Redoing", "Redone."),
    "copy": ("cmd+c", "Copying", "Copied."), "copy that": ("cmd+c", "Copying", "Copied."),
    "copy this": ("cmd+c", "Copying", "Copied."), "copy it": ("cmd+c", "Copying", "Copied."),
    "paste": ("cmd+v", "Pasting", "Pasted."), "paste it": ("cmd+v", "Pasting", "Pasted."),
    "paste that": ("cmd+v", "Pasting", "Pasted."), "paste here": ("cmd+v", "Pasting", "Pasted."),
    "cut": ("cmd+x", "Cutting", "Cut."), "cut that": ("cmd+x", "Cutting", "Cut."),
    "select all": ("cmd+a", "Selecting all", "Selected all."),
    "select everything": ("cmd+a", "Selecting all", "Selected all."),
    "zoom in": ("cmd+=", "Zooming in", "Zoomed in."), "zoom out": ("cmd+-", "Zooming out", "Zoomed out."),
}
# Back and forward are cmd+[ and cmd+] in browsers and Finder; elsewhere they may indent text.
HISTORY_APPS = BROWSERS | {"finder", "system settings", "app store", "music", "podcasts", "books"}


def _count(text: str) -> tuple[str, int]:
    match = REPEAT.search(text)
    if not match:
        return text, 1
    word = match.group("word") or match.group("n")
    count = COUNTS.get(word) or int(word)
    return text[:match.start()].strip(), count


def _chord(words: str) -> str | None:
    """ "command shift t" -> "cmd+shift+t", "enter" -> "return"; None when it is not a key."""
    words = re.sub(r"\s*(?:\+|plus|-)\s*", " ", words).strip()
    if words in KEYS:
        return KEYS[words]
    parts = words.split()
    modifiers = []
    while parts and parts[0] in MODIFIERS:
        modifiers.append(MODIFIERS[parts.pop(0)])
    rest = " ".join(parts)
    if not modifiers or not rest:
        return None
    key = KEYS.get(rest) or (rest if re.fullmatch(r"[a-z0-9]", rest) else None)
    if key is None:
        return None
    return "+".join([*dict.fromkeys(modifiers), key])


def spoken_keys(keys: str) -> str:
    *modifiers, key = keys.split("+")
    name = KEY_NAMES.get(key) or (key.upper() if len(key) == 1 else key.title())
    return " ".join([*(SPOKEN_MODIFIERS[m] for m in modifiers), name])


def _keys(keys: str, count: int, caption: str, summary: str) -> Plan:
    return Plan([ToolCall(f"command-{n}", "press_key", {"keys": keys}) for n in range(1, count + 1)],
                caption, summary)


def plan(utterance: str, app: str | None) -> Plan | None:
    """The steps for a one-step command, or None to leave it to the model.

    With app None the grammar alone decides, as if every app-specific shortcut applied: the
    loop uses that to skip reading the frontmost app for requests that can never match.
    """
    text = _clean(utterance)
    text = re.sub(r"[.,!?;:]", " ", text)
    text = re.sub(r"^(?:just|now)\s+|\s+(?:now|for me)$", "", " ".join(text.split()))
    if not text:
        return None
    def app_in(apps) -> bool:
        return app is None or app.strip().casefold() in apps
    text, count = _count(text)
    count = min(count, MAX_REPEAT)
    times = "" if count == 1 else (" twice" if count == 2 else f" {count} times")

    press = PRESS.match(text)
    if press or text in {"enter", "escape", "hit enter"}:
        keys = _chord(press.group("keys") if press else text)
        if keys is None:
            return None  # "press call" names a button: the model finds it on the screen
        name = spoken_keys(keys)
        return _keys(keys, count, f"Pressing {name}{times}", f"Pressed {name}{times}.")

    scroll = SCROLL.match(text) or PAGE.match(text)
    if scroll:
        direction = scroll.group("dir")
        return Plan([ToolCall(f"command-{n}", "scroll", {"direction": direction}) for n in range(1, count + 1)],
                    f"Scrolling {direction}{times}", f"Scrolled {direction}{times}.")
    end = SCROLL_END.match(text)
    if end and count == 1:
        keys = "cmd+up" if end.group("end") == "top" else "cmd+down"
        return _keys(keys, 1, f"Going to the {end.group('end')}", f"I went to the {end.group('end')}.")

    if NEXT_TAB.match(text):
        return _keys("ctrl+tab", count, "Next tab", f"Switched to the next tab{times}.")
    if PREVIOUS_TAB.match(text):
        return _keys("ctrl+shift+tab", count, "Previous tab", f"Switched to the previous tab{times}.")
    numbered = NUMBERED_TAB.match(text)
    if numbered and app_in(BROWSERS) and count == 1:
        n = numbered.group("n")
        n = ORDINALS[numbered.group("ord")] if n is None else (COUNTS.get(n) or int(n))
        which = "the last tab" if n == 9 else f"tab {n}"
        return _keys(f"cmd+{n}", 1, f"Going to {which}", f"Switched to {which}.")
    if REOPEN_TAB.match(text) and app_in(BROWSERS) and count == 1:
        return _keys("cmd+shift+t", 1, "Reopening the closed tab", "I reopened the last closed tab.")

    if app_in(HISTORY_APPS):
        if BACK.match(text):
            return _keys("cmd+[", count, "Going back", f"Went back{times}.")
        if FORWARD.match(text):
            return _keys("cmd+]", count, "Going forward", f"Went forward{times}.")
    if RELOAD.match(text) and app_in(BROWSERS) and count == 1:
        return _keys("cmd+r", 1, "Reloading", "Reloaded the page.")

    shortcut = SHORTCUTS.get(text)
    if shortcut and (count == 1 or text.startswith(("undo", "redo", "zoom"))):
        keys, caption, summary = shortcut
        return _keys(keys, count, caption, summary if count == 1 else f"{summary[:-1]}{times}.")
    return None
