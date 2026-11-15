"""Color filters for color blindness ("help me tell red from green", "turn off the color filter"), run in code.

The Helper turns macOS's own Color Filters on and off (helper/BeaconHelper/Display/ColorFilterService.swift,
GET/POST /display/color_filter) and answers only once the change reads back. This module decides what the
user asked for and keeps what Beacon changed, so a later "try the other one", "turn it off" or "put my colors
back" acts on it. No model call: a wrong guess here would change the whole screen.

Beacon never guesses which red-green filter suits someone. A general request asks the user to pick, and
"try the other one" switches so they can compare for themselves.
"""

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .helper_client import HelperError

LOGGER = logging.getLogger(__name__)
RED_GREEN = ("deuteranopia", "protanopia")
OTHER = {"deuteranopia": "protanopia", "protanopia": "deuteranopia"}
# How Beacon says each filter, with the name System Settings shows for it.
NAMES = {
    "deuteranopia": "the deuteranopia filter (Green/Red in System Settings)",
    "protanopia": "the protanopia filter (Red/Green in System Settings)",
    "tritanopia": "the tritanopia filter (Blue/Yellow in System Settings)",
    "grayscale": "the grayscale filter",
    "tint": "the color tint filter",
}
CHOOSE_RED_GREEN = ("I can turn on a color filter for red-green color blindness. There are two kinds, and people "
                    "find different ones helpful. Would you like to try deuteranopia or protanopia?")
CHOOSE_ANY = ("Which color filter would you like? For red-green color blindness there's deuteranopia or "
              "protanopia. There's also tritanopia for blue-yellow, and grayscale.")
CAPTIONS = {"enable": "Changing the color filter", "other": "Switching the color filter",
            "off": "Turning off the color filter", "restore": "Restoring your color filter",
            "status": "Checking the color filter", "choose": "Color filters"}
SETTINGS_PATH = "System Settings, Accessibility, Display, Color Filters"
RECENT_S = 600  # how long "try the other one" refers to the filter Beacon turned on

# Spoken names for each filter. "Red-green" alone is left out on purpose: people use it for both kinds.
FILTER_NAMES = {
    "deuteranopia": r"deuter\w*|deutan\w*|duteran\w*",
    "protanopia": r"protan\w*|proton\s?opia",
    "tritanopia": r"tritan\w*|blue\s+(?:and\s+)?yellow",
    "grayscale": r"gr[ae]y\s?scale|black\s+and\s+white",
}
COLOR = r"colou?r"
COLOR_FILTER = (rf"(?:{COLOR}\s+filters?|{COLOR}\s?blind(?:ness)?\s+(?:mode|settings?|filters?)|"
                r"display\s+filters?)")
RED_GREEN_WORDS = r"red\s+(?:and\s+|or\s+|from\s+|vs\s+|versus\s+)?green|green\s+(?:and\s+|or\s+|from\s+)?red"
# Words that make "red and green" a request about seeing colors, not about red and green things on screen.
HELP_WORDS = (r"distinguish|tell\s+(?:\w+\s+)?apart|tell\s+the\s+difference|tell\s+\w+\s+from|"
              r"(?:hard|difficult|easier)\s+to\s+(?:see|tell|distinguish)|trouble\s+(?:seeing|telling|with)|"
              r"(?:can'?t|cannot|can\s+not|struggle\s+to|don'?t)\s+(?:see|tell|distinguish)|"
              rf"help\s+me\s+(?:see|tell)|{COLOR}\s?blind\w*|filters?|modes?|settings?")
ON_WORDS = (r"turn\s+on|switch\s+on|enable|activate|apply|use|try|start|put\s+on|give\s+me|set|switch\s+to|"
            r"want|need|change\s+to")
OFF = re.compile(r"\b(?:turn|switch|shut)\s+(?:\w+\s+){0,3}off\b|\bdisable\b|\bremove\b|\bget\s+rid\s+of\b"
                 r"|\bno\s+more\b|\bdeactivate\b")
RESTORE = re.compile(r"\b(?:restore|revert|undo|put\s+(?:\w+\s+){0,3}back|change\s+(?:\w+\s+){0,3}back|"
                     r"go\s+back\s+to)\b")
NORMAL = re.compile(rf"\b(?:(?:my\s+|the\s+)?(?:{COLOR}s|screen)\s+back\s+to\s+normal|normal\s+{COLOR}s|"
                    rf"(?:put|change|set|turn)\s+(?:my\s+|the\s+)?{COLOR}s\s+back)\b")
OTHER_WORDS = re.compile(r"\b(?:other|another|different|switch|swap|alternate|alternative)\b")
BARE_OTHER = re.compile(r"^(?:(?:ok(?:ay)?|now|then|can\s+you|could\s+you|let'?s|please)\s+)*"
                        r"(?:try|use|switch\s+to|give\s+me|show\s+me|what\s+about|how\s+about)?\s*"
                        r"(?:the\s+)?(?:other|another|different)\s+(?:one|kind|type|filter)"
                        r"(?:\s+(?:instead|please|then|now))*$")
STATUS = re.compile(r"^(?:is|are)\s+(?:the\s+|my\s+|a\s+|any\s+)?(?:\w+\s+){0,2}filters?\s+"
                    rf"(?:on|enabled|active|turned\s+on)\b|^(?:which|what)\s+{COLOR}\s+filter\s+(?:is|am\s+i)")
OTHER_TASK = re.compile(r"^(?:(?:can|could|would)\s+you\s+)?(?:search|google|look\s+up|find|read|open|type|write|"
                        r"send|text|email|message|reply|summarize|tell\s+me\s+about)\b")
COLOR_TROUBLE = re.compile(rf"\b(?:see|tell|distinguish|seeing|telling)\s+(?:the\s+)?{COLOR}s\b"
                           rf"|\btrouble\s+(?:with|seeing)\s+(?:the\s+)?{COLOR}s\b")
REQUEST_WORDS = re.compile(r"\b(?:help|filters?|settings?|modes?|easier|fix|turn|make|adjust|change|way\s+to)\b")
QUESTION = re.compile(r"^(?:what|whats|what's|how|why|who|does|do|is|are|explain|tell\s+me|define|describe)\b")


@dataclass(frozen=True)
class Intent:
    kind: str  # "enable", "choose", "other", "off", "restore" or "status"
    filter: str | None = None  # for "enable"
    red_green: bool = True  # for "choose": offer only the two red-green filters


def words(text: str) -> str:
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[-/]", " ", text)
    text = re.sub(r"[^\w' ]", " ", text)
    text = re.sub(r"^(?:(?:hey\s+)?beacon|please|ok(?:ay)?|so|um+|uh+)\s+", "", " ".join(text.split()))
    return " ".join(text.split())


def named_filter(text: str) -> str | None:
    found = [name for name, pattern in FILTER_NAMES.items() if re.search(rf"\b(?:{pattern})\b", text)]
    return found[0] if len(found) == 1 else None


def parse(text: str, *, recent: bool = False) -> Intent | None:
    """What a request asks of the color filter, or None when it isn't about one.

    `recent` means the request just before this one was about the color filter, so "try the
    other one" and "turn it off" refer to it.
    """
    t = words(text)
    if not t:
        return None
    name = named_filter(t)
    color_filter = bool(re.search(rf"\b{COLOR_FILTER}\b", t))
    red_green = bool(re.search(rf"\b(?:{RED_GREEN_WORDS})\b", t))
    # "the other red-green filter" is a color filter, though not called one.
    filterish = color_filter or name is not None or bool(red_green and re.search(r"\bfilters?\b", t))
    about_filter = filterish or bool(recent and re.search(r"\bfilters?\b|\bit\b|\bthat\b", t))

    if STATUS.search(t) and filterish:
        return Intent("status")
    if NORMAL.search(t):  # "put my colors back to normal" is about the display however it is said
        return Intent("restore")
    if about_filter and RESTORE.search(t):
        return Intent("restore")
    if about_filter and OFF.search(t):
        return Intent("off")
    if recent and BARE_OTHER.match(t):
        return Intent("other")
    if name is not None and (re.search(rf"\b(?:{ON_WORDS})\b", t) or not QUESTION.match(t)):
        return Intent("enable", name)  # a named filter wins: "switch to protanopia instead"; "what is X?" doesn't
    if about_filter and OTHER_WORDS.search(t):
        return Intent("other")
    # "How common is red-green color blindness?" asks for facts; "is there a way to make red and green
    # easier to see?" asks for help.
    asks_facts = bool(QUESTION.match(t) and not REQUEST_WORDS.search(t))
    if red_green and re.search(rf"\b(?:{HELP_WORDS})\b", t) and not asks_facts:
        return Intent("choose")
    if re.search(rf"\b(?:i'?m|i\s+am)\s+{COLOR}\s?blind\b", t):
        return Intent("choose", red_green=False)
    if color_filter and re.search(rf"\b(?:{ON_WORDS})\b", t):
        return Intent("choose", red_green=False)
    # "Adjust my computer's colors for color blindness", "make the screen colorblind friendly": left to the
    # model, these become a refusal (it may not touch System Settings), so they are handled here too.
    # "What is color blindness?" and "search for color blindness tests" are not requests to change the screen.
    if re.search(rf"\b{COLOR}\s?blind\w*", t) and not asks_facts and not OTHER_TASK.match(t):
        return Intent("choose", red_green=red_green)
    if COLOR_TROUBLE.search(t) and not OTHER_TASK.match(t):
        return Intent("choose", red_green=False)
    return None


CHOICE_FIRST = re.compile(r"\b(?:first|1st|former)\b")
CHOICE_SECOND = re.compile(r"\b(?:second|2nd|latter)\b")
EITHER = re.compile(r"\b(?:either|whichever|any|you\s+(?:pick|choose|decide)|don'?t\s+(?:know|care|mind)|not\s+sure|"
                    r"doesn'?t\s+matter|surprise\s+me|up\s+to\s+you)\b")
DECLINE = re.compile(r"^(?:no|nope|neither|cancel|never\s?mind|stop|don'?t|not\s+now)\b")


def choice(reply: str | None, *, red_green: bool = True) -> str | None:
    """What a reply to the "which filter?" question picks: a filter name, "either", "decline",
    or None when it isn't clear."""
    if not reply:
        return None
    t = words(reply)
    name = named_filter(t)
    if name is not None and (not red_green or name in RED_GREEN):
        return name
    if DECLINE.match(t):
        return "decline"
    first, second = bool(CHOICE_FIRST.search(t)), bool(CHOICE_SECOND.search(t))
    if first != second:
        return "deuteranopia" if first else "protanopia"
    # System Settings calls them Green/Red and Red/Green.
    if re.fullmatch(r"(?:the\s+)?green\s+red(?:\s+(?:one|filter))?", t):
        return "deuteranopia"
    if re.fullmatch(r"(?:the\s+)?red\s+green(?:\s+(?:one|filter))?", t):
        return "protanopia"
    if EITHER.search(t):
        return "either"
    return None


class Unavailable(Exception):
    """The Helper can't change color filters here (another OS, or an unsupported macOS)."""


def unavailable_message() -> str:
    return f"I can't change color filters on this computer. On a Mac, you can turn them on in {SETTINGS_PATH}."


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


class Controller:
    """Applies color-filter intents through the Helper and remembers what Beacon changed."""

    def __init__(self, helper, clock=time.monotonic):
        self.helper = helper
        self.clock = clock
        self.prior: dict | None = None  # the state before Beacon's first change, for "put my colors back"
        self.applied: str | None = None  # the filter Beacon last turned on
        self.changed_at = float("-inf")
        self.last_task: int | None = None  # the Brain's task number of the latest color-filter request
        self.failed = False  # the latest run could not make or confirm the change (for the log)

    def recent(self, task_number: int) -> bool:
        """The request before `task_number` was about the color filter, a short while ago."""
        return (self.last_task is not None and task_number - self.last_task <= 1
                and self.clock() - self.changed_at <= RECENT_S)

    async def _call(self, payload: dict | None = None) -> dict:
        try:
            return await self.helper.request("/display/color_filter", payload)
        except HelperError as error:
            if error.code == "action_unsupported":
                raise Unavailable(str(error)) from error
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {404, 405}:  # a Helper without the endpoint (Windows, older builds)
                raise Unavailable(str(error)) from error
            raise

    async def current(self) -> dict:
        return await self._call()

    async def _set(self, enabled: bool, filter_name: str | None) -> dict:
        payload = {"enabled": enabled, **({"filter": filter_name} if filter_name else {})}
        result = await self._call(payload)
        # The Helper verifies before answering; check its answer too rather than trust "ok" alone.
        if (result.get("enabled") is not enabled or result.get("verified") is not True
                or (filter_name is not None and result.get("filter") != filter_name)):
            raise HelperError("action_failed", f"Color filter read back as {result}")
        return result

    def _remember_prior(self, state: dict) -> None:
        if self.prior is None:
            self.prior = {"enabled": bool(state.get("enabled")), "filter": state.get("filter")}

    def _changed(self, applied: str | None) -> None:
        self.applied, self.changed_at = applied, self.clock()

    @staticmethod
    def _next_hint(filter_name: str) -> str:
        if filter_name in OTHER:
            return ' Say "try the other one" to compare, or "turn off the color filter" to undo it.'
        return ' Say "turn off the color filter" to undo it.'

    async def enable(self, filter_name: str, *, switching: bool = False) -> str:
        state = await self.current()
        name = NAMES[filter_name]
        if state.get("enabled") and state.get("filter") == filter_name:
            self._changed(filter_name)
            return f"{_capital(name)} is already on.{self._next_hint(filter_name)}"
        self._remember_prior(state)
        try:
            await self._set(True, filter_name)
        except HelperError as error:
            LOGGER.warning("Color filter change failed: %s", error)
            self.failed = True
            return (f"I tried to turn on {name}, but I couldn't confirm that it changed, so it may not have "
                    f"worked. You can check in {SETTINGS_PATH}.")
        self._changed(filter_name)
        return f"{'Switched to' if switching else 'I turned on'} {name}.{self._next_hint(filter_name)}"

    async def other(self) -> str | None:
        """Switch between the two red-green filters; None when there's no red-green filter to switch from."""
        recent = self.clock() - self.changed_at <= RECENT_S
        current = self.applied if recent and self.applied in OTHER else None
        if current is None:
            state = await self.current()
            if state.get("enabled") and state.get("filter") in OTHER:
                current = state["filter"]
        if current is None:
            return None
        return await self.enable(OTHER[current], switching=True)

    async def off(self) -> str:
        state = await self.current()
        if not state.get("enabled"):
            self.applied = None
            return "The color filter is already off."
        # Leave System Settings showing the filter type the user had chosen before Beacon changed it.
        prior_filter = (self.prior or {}).get("filter")
        restore_type = prior_filter if prior_filter in NAMES and prior_filter != state.get("filter") else None
        try:
            await self._set(False, restore_type)
        except HelperError as error:
            LOGGER.warning("Color filter change failed: %s", error)
            self.failed = True
            return ("I tried to turn off the color filter, but I couldn't confirm that it changed. "
                    f"You can turn it off in {SETTINGS_PATH}.")
        self._changed(None)
        prior = self.prior
        if prior is not None and prior["enabled"]:
            return (f"I turned off the color filter. Before I changed it, you had "
                    f"{NAMES.get(prior['filter'], 'another filter')} on. Say \"put my colors back\" to return to it.")
        self.prior = None
        return "I turned off the color filter. Your colors are back to normal."

    async def restore(self) -> str:
        """Put the color filter back to how it was before Beacon first changed it."""
        if self.prior is None:
            return await self.off()
        prior = self.prior
        filter_name = prior.get("filter") if prior.get("filter") in NAMES else None
        state = await self.current()
        if bool(state.get("enabled")) == prior["enabled"] and (
                not prior["enabled"] or state.get("filter") == filter_name):
            self.prior = None
            self._changed(None)
            return "Your color filter is already back to how it was."
        if prior["enabled"] and filter_name is None:
            self.failed = True
            return f"I couldn't tell which filter you had before. You can choose it in {SETTINGS_PATH}."
        try:
            await self._set(prior["enabled"], filter_name)
        except HelperError as error:
            LOGGER.warning("Color filter restore failed: %s", error)
            self.failed = True
            return f"I tried to put your color filter back, but I couldn't confirm it. You can check in {SETTINGS_PATH}."
        self.prior = None
        self._changed(None)
        if prior["enabled"]:
            return f"I put your color filter back to {NAMES[filter_name]}."
        return "I turned off the color filter. Your colors are back to how they were."

    async def status(self) -> str:
        state = await self.current()
        if not state.get("enabled"):
            return "No color filter is on."
        return f"{_capital(NAMES.get(state.get('filter'), 'a color filter'))} is on."

    async def run(self, intent: Intent, ask) -> str:
        """Carry out an intent and return what to say. `ask(question)` speaks a question and
        returns the reply, or None when there was none."""
        self.failed = False
        try:
            if intent.kind == "status":
                return await self.status()
            if intent.kind == "off":
                return await self.off()
            if intent.kind == "restore":
                return await self.restore()
            if intent.kind == "enable":
                return await self.enable(intent.filter)
            if intent.kind == "other":
                switched = await self.other()
                if switched is not None:
                    return switched
                intent = Intent("choose")  # nothing to switch from: offer both rather than guess
            reply = await ask(CHOOSE_RED_GREEN if intent.red_green else CHOOSE_ANY)
            picked = choice(reply, red_green=intent.red_green)
            if picked == "decline" or not reply:
                return "Okay, I left your colors as they are."
            if picked is None:
                return ("I didn't catch which one, so I left your colors as they are. You can say "
                        '"turn on the deuteranopia filter" or "turn on the protanopia filter".')
            if picked == "either":
                # Not a recommendation: one of the two has to come first.
                return "I'll start with deuteranopia. " + await self.enable("deuteranopia")
            return await self.enable(picked)
        except Unavailable:
            self.failed = True
            return unavailable_message()
        except (HelperError, httpx.HTTPError, KeyError, ValueError, TypeError):
            LOGGER.warning("Color filter request failed", exc_info=True)
            self.failed = True
            return ("Something went wrong with the color filter, and I couldn't confirm whether it changed. "
                    f"You can check in {SETTINGS_PATH}.")
