"""Simple browser requests run in code, with no model round trip.

"Open a new tab and search amazon", "search for cats", "go to youtube": the 2B model asks
where to search or which control to click, and grounding turns "open a new tab and ..." into
"What should I click instead?". With a browser frontmost these are keyboard steps every
browser shares: cmd+T opens a tab with the address bar focused, cmd+L focuses the address
bar, and typing there then Return searches or loads the site.
"""

import re
from dataclasses import dataclass

from .llm import ToolCall
from .page_summary import BROWSERS

LEAD = re.compile(r"^(?:(?:please|hey beacon|beacon|ok|okay|can you|could you|would you|will you|"
                  r"go ahead and|i want to|i'd like to|i would like to)\s+)+")
NEW_TAB = re.compile(r"^(?:open|make|create|start|add|give me)\s+(?:up\s+)?(?:a\s+)?(?:new|another|fresh)\s+tab"
                     r"|^new\s+tab")
THEN = re.compile(r"^(?:,\s*)?(?:(?:and|then)\s+)+(?:then\s+)?|^,\s*")
SEARCH = re.compile(r"^(?:search(?:\s+(?:for|up))?|google|look\s+up)\s+(?P<query>.+)$")
GO_TO = re.compile(r"^(?:go\s+to|navigate\s+to|visit|take\s+me\s+to|bring\s+up|load|pull\s+up|open(?:\s+up)?)"
                   r"\s+(?P<site>.+)$")
WEB_SUFFIX = re.compile(r"\s+(?:on\s+(?:google|the\s+web|the\s+internet|the\s+browser|chrome|safari)"
                        r"|in\s+(?:google|the\s+browser|chrome|safari|the\s+new\s+tab|a\s+new\s+tab))$")
SITE_WORDS = re.compile(r"\s+(?:website|web\s+site|site|homepage|home\s+page)$")
DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?$")
# "search for X on this page", "go to the next page": about the page itself, not the web.
ON_PAGE = re.compile(r"\b(?:on|in|from)\s+(?:this|the|my)\s+(?:page|screen|tab|window|site|document|email)\b"
                     r"|\bhere$")
NOT_SITES = {
    "top", "bottom", "next", "previous", "back", "settings", "preferences", "home", "start", "end", "it",
    "there", "that", "this", "tab", "page", "link", "inbox", "sleep", "bed", "the", "a", "an", "window",
    "address", "bar", "field", "search", "menu", "downloads", "history", "bookmarks", "first", "last",
}


@dataclass
class Plan:
    steps: list[ToolCall]
    caption: str  # spoken while acting ("Opening a new tab")
    summary: str  # spoken when done ("I opened a new tab and searched for amazon.")


def _clean(text: str) -> str:
    text = text.casefold().strip()
    text = re.sub(r"[\"“”]", "", text)
    text = re.sub(r"[.!?]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    text = LEAD.sub("", text)
    return re.sub(r"[\s,]+please$", "", text).strip()


def _site(words: str, explicit: bool) -> str | None:
    """The address to type for "go to <words>", or None when it may not be a website."""
    words = re.sub(r"\s+dot\s+", ".", words.strip())
    words = re.sub(r"^(?:the|a)\s+", "", words)
    named_site = bool(SITE_WORDS.search(words))
    words = SITE_WORDS.sub("", words).strip()
    if DOMAIN.fullmatch(words) or re.fullmatch(r"https?://\S+", words):
        return words
    if not re.fullmatch(r"[a-z0-9-]+", words) or words in NOT_SITES:
        return None
    # "open notes" may mean the Notes app: "open" needs "website" or a domain.
    return f"{words}.com" if explicit or named_site else None


MORE_STEPS = re.compile(r"\bthen\b|,\s*and\b|\band\s+(?:open|click|press|tap|read|tell|play|go|scroll|select|"
                        r"choose|type|send|show|pick|watch|buy|add|sign|log)\b")
IN_NEW_TAB = re.compile(r"\s+in\s+(?:a\s+)?new\s+tab$")


def _web_step(rest: str, *, in_new_tab: bool = False) -> tuple[str, str, str] | None:
    """(text to type, caption, summary) for a search or go-to phrase, or None."""
    if ON_PAGE.search(rest) or MORE_STEPS.search(rest):
        return None
    rest = WEB_SUFFIX.sub("", rest).strip()
    search = SEARCH.match(rest)
    if search:
        query = search.group("query").strip()
        if not query or query in {"it", "that", "this", "something"}:
            return None
        on_site = re.fullmatch(r"(?P<site>[a-z0-9-]+(?:\.[a-z]{2,})?)\s+for\s+(?P<query>.+)", query)
        if on_site:  # "search amazon for headphones"
            return (f"{on_site.group('site')} {on_site.group('query')}",
                    f"Searching {on_site.group('site')} for {on_site.group('query')}",
                    f"searched {on_site.group('site')} for {on_site.group('query')}")
        return query, f"Searching for {query}", f"searched for {query}"
    go = GO_TO.match(rest)
    if go:
        explicit = in_new_tab or not rest.startswith("open")
        site = _site(go.group("site"), explicit)
        if site:
            return site, f"Going to {site}", f"went to {site}"
    return None


def plan(utterance: str, app: str) -> Plan | None:
    """The keyboard steps for a simple browser request, or None to leave it to the model."""
    if app.casefold() not in BROWSERS:
        return None
    text = _clean(utterance)
    suffix = IN_NEW_TAB.search(text)
    if suffix and not NEW_TAB.match(text):
        # "Open youtube in a new tab" is "open a new tab and go to youtube".
        web = _web_step(text[:suffix.start()], in_new_tab=True)
        if web is None:
            return None
        typed, caption, summary = web
        return Plan([ToolCall("browser-1", "press_key", {"keys": "cmd+t"}),
                     ToolCall("browser-2", "type_text", {"text": typed}),
                     ToolCall("browser-3", "press_key", {"keys": "return"})],
                    f"Opening a new tab and {caption[0].lower()}{caption[1:]}",
                    f"I opened a new tab and {summary}.")
    new_tab = NEW_TAB.match(text)
    if new_tab:
        rest = text[new_tab.end():].strip()
        rest = re.sub(r"^(?:in\s+(?:the\s+)?(?:browser|chrome|safari)|for\s+me)\b\s*", "", rest)
        open_tab = ToolCall("browser-1", "press_key", {"keys": "cmd+t"})
        if not rest:
            return Plan([open_tab], "Opening a new tab", "I opened a new tab.")
        joined = THEN.match(rest)
        web = _web_step(rest[joined.end():], in_new_tab=True) if joined else None
        if web is None:
            return None  # "open a new tab and read it to me": more than this fast path knows
        typed, caption, summary = web
        return Plan([open_tab, ToolCall("browser-2", "type_text", {"text": typed}),
                     ToolCall("browser-3", "press_key", {"keys": "return"})],
                    f"Opening a new tab and {caption[0].lower()}{caption[1:]}",
                    f"I opened a new tab and {summary}.")
    web = _web_step(text)
    if web is None:
        return None
    typed, caption, summary = web
    return Plan([ToolCall("browser-1", "press_key", {"keys": "cmd+l"}),
                 ToolCall("browser-2", "type_text", {"text": typed}),
                 ToolCall("browser-3", "press_key", {"keys": "return"})],
                caption, f"I {summary}.")
