"""One-step commands ("press enter", "next tab", "go back", "scroll down") run in code.

The model takes 3 to 18 seconds to turn "press enter" into press_key(return), and the loop
reads the whole window first. These requests name a key or a shortcut every Mac app shares,
so a short grammar turns them into the step straight away: the Brain only needs the
frontmost app's name (a one-element snapshot, ~10 ms) for the risk check and the few
shortcuts that differ between apps. Anything the grammar does not match whole, such as
"press call" or "go back to the inbox", is left to the model.
"""

import difflib
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
SCROLL = re.compile(r"^scroll(?:\s+(?:the\s+|this\s+)?(?:page|window|screen))?\s+(?P<dir>up|down|left|right)"
                    r"(?:\s+(?:this|the)\s+(?:page|window|screen))?"
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


# "Scroll through the results", "keep scrolling", "scroll down slowly": scroll the front window
# steadily, in small wheel steps, until the user says stop or presses the talk key.
PACE_WORDS = (r"(?:really\s+|very\s+|nice\s+and\s+|a\s+(?:bit|little)\s+)?(?:slowly|slow|gently|gradually|steadily|"
              r"smoothly|continuously|constantly|nonstop|non\s+stop|quickly|fast|faster|slower)")
CONTINUOUS = re.compile(
    rf"^(?P<lead>(?:{PACE_WORDS}\s+)*)"
    r"(?P<verb>(?:keep(?:\s+on)?|start|begin|continue|go\s+on)\s+scrolling|auto\s*scroll(?:ing)?|scroll(?:ing)?)"
    r"(?:\s+(?:the\s+|this\s+)?(?:page|window|screen))?"
    r"(?:\s+(?P<dir>up|down|left|right)(?:wards?)?)?"
    r"(?:\s+(?:this|the)\s+(?:page|window|screen))?"
    rf"(?P<pace>(?:\s+{PACE_WORDS})*)"
    r"(?:\s+(?P<through>through|along|over)(?P<what>(?:\s+[\w']+){0,5}))?"
    rf"(?P<tail>(?:\s+{PACE_WORDS})*)$")
CONTINUOUS_END = re.compile(r"\s+(?:until\s+i\s+(?:say|tell\s+you)(?:\s+to)?\s+stop|for\s+(?:a\s+(?:while|bit)|me)|"
                            r"please|now|for\s+a\s+few\s+seconds|so\s+i\s+can\s+see(?:\s+them|\s+it)?)$")
MORE_THAN_SCROLL = re.compile(r"\b(?:and|then|to|until|find|for|click|open|read|look|stop|when|type)\b")
SLOW = re.compile(r"\b(?:slow|slowly|slower|gently|gradually)\b")
FAST = re.compile(r"\b(?:quickly|fast|faster)\b")
SCROLL_SPEEDS = {"slow": 80, "normal": 150, "fast": 300}  # pixels per second (about half of the first cut)


class Glide:
    """A continuous scroll: which way and how fast (pixels per second)."""

    def __init__(self, direction: str, speed: str):
        self.direction, self.speed = direction, speed
        self.pixels_per_second = SCROLL_SPEEDS[speed]

    def __repr__(self) -> str:
        return f"Glide({self.direction!r}, {self.speed!r})"


def continuous_scroll(utterance: str) -> Glide | None:
    """ "Scroll through the results", "keep scrolling", "scroll down slowly" -> a Glide, else None.

    A plain "scroll down" is one page (plan); only a word that asks for continuing ("keep",
    "through", "slowly") makes it continuous.
    """
    text = _clean(utterance)
    text = " ".join(re.sub(r"[.,!?;:]", " ", text).split())
    text = re.sub(r"^(?:just|now|ok(?:ay)?|so|hey\s+beacon|beacon)\s+", "", text)
    previous = None
    while previous != text:
        previous, text = text, CONTINUOUS_END.sub("", text).strip()
    match = CONTINUOUS.match(text)
    if not match:
        return None
    verb = match.group("verb")
    paced = (match.group("lead") + match.group("pace") + match.group("tail")).strip()
    plain = verb.startswith("scroll")
    if plain and not paced and not match.group("through"):
        return None  # "scroll down": one page, handled by plan()
    what = (match.group("what") or "").strip()
    if MORE_THAN_SCROLL.search(what):
        return None  # "scroll through the results and click the first one": the model plans it
    speed = "slow" if SLOW.search(text) else "fast" if FAST.search(text) else "normal"
    return Glide(match.group("dir") or "down", speed)


# "Open messages", "launch discord", "switch to chrome", "bring up the notes app".
OPEN_APP = re.compile(r"^(?:open(?:\s+up)?|launch|start(?:\s+up)?|run|switch\s+(?:over\s+)?(?:to|into)|go\s+(?:over\s+)?to|"
                      r"bring\s+up|pull\s+up|show(?:\s+me)?|take\s+me\s+to|get\s+me|bring\s+(?:the\s+)?(?P<front>.+?)"
                      r"\s+(?:to\s+the\s+front|into\s+view|up\s+front|forward))\s*(?P<name>.*)$")
APP_FILLER = re.compile(r"^(?:the|my|a)\s+|\s+(?:app|application|program|window)$|^(?:apple|mac)\s+")
APP_ALIASES = {
    "settings": "System Settings", "system preferences": "System Settings", "preferences": "System Settings",
    "browser": "Safari", "web browser": "Safari", "internet": "Safari", "email": "Mail", "mail app": "Mail",
    "itunes": "Music", "apple music": "Music", "files": "Finder", "file browser": "Finder",
    "text editor": "TextEdit", "calc": "Calculator", "chrome": "Google Chrome", "facetime": "FaceTime",
    "face time": "FaceTime", "imessage": "Messages", "imessages": "Messages", "texts": "Messages",
    "text messages": "Messages", "message": "Messages", "note": "Notes", "app store": "App Store",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "word": "Microsoft Word",
    "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint", "voice memos": "VoiceMemos",
    "find my": "FindMy", "quicktime": "QuickTime Player", "zoom": "zoom.us", "photo": "Photos",
    "reminder": "Reminders", "calendars": "Calendar", "contact": "Contacts", "maps app": "Maps",
}


def _app_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold().removesuffix(".app"))


def app_request(utterance: str) -> tuple[str, bool] | None:
    """(the spoken app name, whether the verb was "go to") for "open X"-style requests, or None."""
    text = " ".join(re.sub(r"[.,!?;:]", " ", _clean(utterance)).split())
    text = re.sub(r"\s+(?:for\s+me|now|right\s+now|please)$", "", text)
    match = OPEN_APP.match(text)
    if not match:
        return None
    name = (match.group("front") or match.group("name") or "").strip()
    if match.group("front") and match.group("name"):
        return None
    previous = None
    while previous != name:
        previous, name = name, APP_FILLER.sub("", name).strip()
    if not name or len(name.split()) > 4 or MORE_APP_STEPS.search(name):
        return None
    return name, text.startswith(("go ", "take me", "show"))


MORE_APP_STEPS = re.compile(r"\b(?:and|then|tab|tabs|website|site|page|link|\.com|on|in|from|with|to|for)\b")


def match_app(spoken: str, running: list[str], installed: list[str]) -> str | None:
    """The installed or running app the user named, or None when it is not clearly an app."""
    names = list(dict.fromkeys([*running, *installed]))
    by_key = {_app_key(name): name for name in names}
    alias = APP_ALIASES.get(spoken)
    if alias and _app_key(alias) in by_key:
        return by_key[_app_key(alias)]
    key = _app_key(spoken)
    if len(key) < 2:
        return None
    if key in by_key:
        return by_key[key]
    # "zoom" -> "zoom.us", "intellij" -> "IntelliJ IDEA": the spoken words begin the app's name.
    starts = sorted((name for name in names if re.split(r"[^a-z0-9]+", name.casefold())[0] == spoken.split()[0]
                     and _app_key(name).startswith(key)), key=len)
    if starts and len(key) >= 4:
        return starts[0]
    # One misheard letter or two ("discored", "safary").
    close = difflib.get_close_matches(key, list(by_key), n=1, cutoff=0.85 if len(key) >= 6 else 0.9)
    return by_key[close[0]] if close else None


WINDOW_CONTROLS = {"close", "close window", "minimize window", "full screen", "zoom", "minimize", "new tab",
                   "back", "forward", "reload"}
PRESSABLE = {"button", "link", "menuitem", "tab", "radio", "checkbox", "popup"}
PRESS_WORDS = re.compile(r"^(?:(?:press|click|tap|hit|push|select|choose|pick|do|go\s+with|i'?ll|i\s+will|let'?s)\s+)?"
                         r"(?:on\s+)?(?:the\s+)?(?P<name>.+?)(?:\s+(?:button|option|link))?$")


def named_button(words: str, snapshot: dict, *, bare: bool = False) -> dict | None:
    """The one visible control that "Fold", "press call" or "check" names, or None.

    The name must be the whole label ("Fold") or its first words before an amount ("Call" for
    "Call $4", "Raise" for "Raise 2.3"), and only one control may fit.
    """
    text = " ".join(re.sub(r"[^\w.' ]", " ", words.casefold()).split()).strip(" .")
    text = re.sub(r"^(?:ok(?:ay)?|yes|yeah|um|uh|so|then|please)\s+|\s+(?:please|now|then)$", "", text)
    match = PRESS_WORDS.match(text)
    if not match or (bare and match.group("name") != text):
        return None  # bare: only a label said on its own ("Fold"), not "click reply"
    name = match.group("name").strip(" .")
    if not name or len(name.split()) > 4:
        return None
    exact, leading = [], []
    for item in snapshot.get("elements", []):
        if item.get("role") not in PRESSABLE or {"disabled", "inferred"} & set(item.get("states", [])):
            continue
        label = " ".join(re.sub(r"[^\w.' ]", " ", (item.get("label") or "").casefold()).split()).strip(" .")
        if not label or label in WINDOW_CONTROLS:
            continue
        if label == name:
            exact.append(item)
        elif label.startswith(name + " ") and re.fullmatch(r"(?:to\s+)?[\d.,]+\s*\w{0,3}|\(\d+\)",
                                                          label[len(name) + 1:].strip()):
            leading.append(item)
    found = exact or leading
    return found[0] if len(found) == 1 else None


CLOCK_TIME = re.compile(r"^(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:tell me|say|give me|read(?: me)?|check)?\s*"
                        r"(?:what\s+(?:time\s+it\s+is|the\s+time\s+is)|the\s+(?:current\s+)?time|what(?:s| is)\s+the\s+"
                        r"(?:current\s+)?time|what\s+time\s+is\s+it|time)(?:\s+(?:is\s+it|now|right\s+now|please|"
                        r"(?:on|from|in)\s+(?:my|the)\s+(?:screen|computer|mac|clock)|here))*$")
CLOCK_DATE = re.compile(r"^(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:tell me\s+)?(?:what(?:s| is)\s+(?:the\s+|today'?s\s+)?"
                        r"(?:date|day)(?:\s+today)?|what\s+day\s+is\s+(?:it|today)|what\s+(?:is\s+)?(?:the\s+)?date\s+is\s+it|"
                        r"the\s+date|today'?s\s+date|what\s+day\s+of\s+the\s+week\s+is\s+it)(?:\s+(?:today|please|now))*$")


def clock_answer(utterance: str, now=None) -> str | None:
    """ "What time is it?", "what's the date?": answered from this Mac's clock, in local time."""
    import datetime

    text = " ".join(re.sub(r"[^\w ]", " ", utterance.casefold().replace("'", "").replace("’", "")).split())
    text = re.sub(r"^(?:hey\s+)?beacon\s+", "", text)
    now = now or datetime.datetime.now()
    hour = now.strftime("%I").lstrip("0")
    clock = f"{hour}:{now.strftime('%M %p')}"
    day = f"{now.strftime('%A, %B')} {now.day}"
    if CLOCK_TIME.match(text):
        return f"It's {clock}."
    if CLOCK_DATE.match(text):
        return f"It's {day}, {now.year}."
    if re.fullmatch(r"(?:what(?:s| is) the )?(?:time and (?:the )?date|date and (?:the )?time)(?: please)?", text):
        return f"It's {clock} on {day}."
    return None
