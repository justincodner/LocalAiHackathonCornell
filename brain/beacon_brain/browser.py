"""Simple browser requests run in code, with no model round trip.

"Open a new tab and search amazon", "search for cats", "go to youtube": the 2B model asks
where to search or which control to click, and grounding turns "open a new tab and ..." into
"What should I click instead?". With a browser frontmost these are keyboard steps every
browser shares: cmd+T opens a tab with the address bar focused, cmd+L focuses the address
bar, and typing there then Return searches or loads the site.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, urlsplit

from .llm import ToolCall
from .page_summary import BROWSERS

LEAD = re.compile(r"^(?:(?:please|hey beacon|beacon|ok|okay|can you|could you|would you|will you|"
                  r"go ahead and|i want to|i'd like to|i would like to)\s+)+")
NEW_TAB = re.compile(r"^(?:(?:open|make|create|start|add|give me|pull up)\s+(?:up\s+)?(?:a\s+)?(?:(?:new|another|fresh)\s+)?tab"
                     r"|^new\s+tab)(?!\w)")
THEN = re.compile(r"^(?:,\s*)?(?:(?:and|then)\s+)+(?:then\s+)?|^,\s*")
SEARCH = re.compile(r"^(?:search(?:\s+(?:for|up))?|google|look\s+up)\s+(?P<query>.+)$")
GO_TO = re.compile(r"^(?:go\s+to|navigate\s+to|visit|take\s+me\s+to|bring\s+up|load|pull\s+up|open(?:\s+up)?)"
                   r"\s+(?P<site>.+)$")
WEB_SUFFIX = re.compile(r"\s+(?:on\s+(?:google|the\s+web|the\s+internet|the\s+browser|chrome|safari)"
                        r"|in\s+(?:google|the\s+browser|chrome|safari|the\s+new\s+tab|a\s+new\s+tab))$")
# Sites whose search page Beacon can load straight from the address bar.
SITE_SEARCH = {
    "amazon": "amazon.com/s?k={}", "youtube": "youtube.com/results?search_query={}", "ebay": "ebay.com/sch/i.html?_nkw={}",
    "wikipedia": "en.wikipedia.org/w/index.php?search={}", "reddit": "reddit.com/search/?q={}",
    "google": "google.com/search?q={}", "github": "github.com/search?q={}", "walmart": "walmart.com/search?q={}",
    "target": "target.com/s?searchTerm={}", "etsy": "etsy.com/search?q={}", "spotify": "open.spotify.com/search/{}",
}
ON_SITE = re.compile(r"(?P<query>.+?),?\s+(?:on|in|at|from|using)\s+(?:the\s+)?(?P<site>" + "|".join(SITE_SEARCH) +
                     r")(?:\.com)?(?:'s)?(?:\s+(?:search\s+bar|search\s+box|search\s+field|search|website|site|page))?")
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
    text = re.sub(r"[.!?,;:\s]+$", "", text).strip()
    # Dictation commas after the verb: "Search, Amazon," is "search amazon".
    text = re.sub(r"^((?:please\s+)?(?:search(?:\s+for)?|google|look\s+up|go\s+to|open))\s*,\s*", r"\1 ", text)
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
IN_NEW_TAB = re.compile(r"\s+(?:in|on|into|onto|using)\s+(?:a\s+|the\s+)?(?:new|another|fresh)\s+tab$")
# "In a new tab, open amazon": the same request with the tab first.
NEW_TAB_FIRST = re.compile(r"^(?:in|on)\s+(?:a\s+|the\s+)?(?:new|another|fresh)\s+tab\s*,?\s*")
# "Open a new tab with amazon", "new tab to youtube.com", "new tab amazon": the site follows directly.
LINK = re.compile(r"^(?:(?:with|to|for|on|at|showing|that\s+(?:opens|goes\s+to|shows))\s+)?")
NOT_BARE_SITES = {"now", "again", "too", "also", "quickly", "first", "real", "quick", "wait", "stop", "please"}


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
        if query in SITE_SEARCH or query.removesuffix(".com") in SITE_SEARCH:  # "search amazon": the site itself
            site = query.removesuffix(".com") + ".com"
            return site, f"Going to {site}", f"went to {site}"
        on_site = (re.fullmatch(r"(?P<site>[a-z0-9-]+(?:\.[a-z]{2,})?)\s+for\s+(?P<query>.+)", query)
                   or ON_SITE.fullmatch(query))
        if on_site:  # "search amazon for headphones", "search for gifts in the amazon search bar"
            site, wanted = on_site.group("site"), on_site.group("query").strip(" ,")
            name = site.split(".")[0]
            url = SITE_SEARCH.get(name)
            typed = url.format(quote_plus(wanted)) if url else f"{site} {wanted}"
            return typed, f"Searching {name.title()} for {wanted}", f"searched {name.title()} for {wanted}"
        return query, f"Searching for {query}", f"searched for {query}"
    go = GO_TO.match(rest)
    if go:
        explicit = in_new_tab or not rest.startswith("open")
        site = _site(go.group("site"), explicit)
        if site:
            return site, f"Going to {site}", f"went to {site}"
    return None


def _bare_site(words: str) -> tuple[str, str, str] | None:
    """ "amazon", "the amazon website", "youtube.com on it" right after "new tab"; None if not a site."""
    words = re.sub(r"\s+(?:on|in)\s+it$", "", WEB_SUFFIX.sub("", words.strip())).strip()
    if not words or words in NOT_BARE_SITES or ON_PAGE.search(words) or MORE_STEPS.search(words):
        return None
    web = _web_step(words, in_new_tab=True)  # "new tab, search for cats"
    if web is not None:
        return web
    site = _site(words, explicit=True)
    return None if site is None else (site, f"Going to {site}", f"went to {site}")


def plan(utterance: str, app: str) -> Plan | None:
    """The keyboard steps for a simple browser request, or None to leave it to the model."""
    if app.casefold() not in BROWSERS:
        return None
    text = _clean(utterance)
    first = NEW_TAB_FIRST.match(text)
    if first and not NEW_TAB.match(text):
        text = f"{text[first.end():]} in a new tab"
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
        if joined:
            web = _web_step(rest[joined.end():], in_new_tab=True)
        else:
            web = _bare_site(rest[LINK.match(rest).end():])
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


NAMED_TAB = [
    re.compile(r"^(?:go|switch|change|move|jump|flip|take\s+me)\s+(?:over\s+|back\s+)*to\s+(?:the\s+|my\s+)?"
               r"(?P<name>.+?)\s+tab$"),
    re.compile(r"^(?:open|show(?:\s+me)?|bring\s+up|pull\s+up)\s+(?:the|my)\s+(?P<name>.+?)\s+tab$"),
    re.compile(r"^(?:go|switch|change|move|jump)\s+(?:over\s+|back\s+)*to\s+the\s+tab\s+(?:with|for|showing|that\s+has)\s+"
               r"(?:the\s+|my\s+)?(?P<name>.+)$"),
]
NOT_TAB_NAMES = {"next", "previous", "last", "first", "new", "other", "that", "this", "same", "a", "second", "third"}
TAB_NOISE = re.compile(r"\s+-\s+(?:memory usage|high memory usage)\b.*$|^\(\d+\)\s*|\s+-\s+(?:audio playing|muted)\b.*$",
                       re.IGNORECASE)


def named_tab(utterance: str, snapshot: dict) -> Plan | None:
    """ "Go to my YouTube tab": click the one tab whose title names it, or None."""
    text = _clean(utterance)
    match = next((m for m in (p.match(text) for p in NAMED_TAB) if m), None)
    if match is None:
        return None
    name = match.group("name").strip()
    if name in NOT_TAB_NAMES or len(name.split()) > 5:
        return None
    wanted = re.findall(r"[a-z0-9]+", name)
    tabs = []
    for item in snapshot.get("elements", []):
        if item.get("role") != "tab":
            continue
        title = TAB_NOISE.sub("", item.get("label") or "")
        shown = re.findall(r"[a-z0-9]+", title.casefold())
        joined = "".join(shown)
        if all(word in shown or word in joined for word in wanted):
            tabs.append((item, title))
    if len(tabs) != 1:
        return None  # no such tab, or several: the model (or a question) sorts it out
    tab, title = tabs[0]
    short = re.sub(r"\s*\|\s*", " ", title.split(" - ")[0]).strip() or name
    if "selected" in tab.get("states", []):
        return Plan([], "", f"You're already on the {short} tab.")
    return Plan([ToolCall("browser-1", "click", {"id": tab["id"]})], f"Switching to {short}", f"Switched to {short}.")


RESULT_COMMAND = re.compile(r"^(?P<verb>click|tap|open|go to|select|choose) (?:on )?(?:the )?(?P<name>.+)$")
RESULT_SUFFIX = re.compile(r"\s+(?:from|in|on) (?:the |these )?(?:(?:google|web|search) )?results?$")
RESULT_KIND = re.compile(r"\s+(?:(?:web|search) )?(?:result|article|website|site|link)$")
GOOGLE_HOST = re.compile(r"(?:www\.)?google\.(?:com(?:\.[a-z]{2})?|co\.[a-z]{2}|[a-z]{2})")
RESULT_BREADCRUMB = re.compile(r"\s+https?://([\w.-]+)(?:/[^\s›]*)?\s*(?:›|$)", re.IGNORECASE)


@dataclass
class ResultRequest:
    name: str
    query: str
    explicit: bool


def search_result_request(utterance: str, snapshot: dict) -> ResultRequest | None:
    """A named result on the active Google results page."""
    text = _clean(utterance)
    match = RESULT_COMMAND.fullmatch(text)
    if snapshot["app"].casefold() not in BROWSERS or not match or MORE_STEPS.search(text):
        return None
    target = match["name"]
    name = RESULT_SUFFIX.sub("", target)
    explicit = name != target or bool(RESULT_KIND.search(name) and not name.endswith(" link"))
    name = RESULT_KIND.sub("", name)
    if not name or name in {"it", "this", "that", "first", "second", "third", "last", "next", "previous",
                            "top", "bottom", "result", "article", "link", "one"}:
        return None
    if not explicit and match["verb"] not in {"click", "tap"}:
        return None
    address = next((item.get("value") for item in snapshot["elements"]
                    if item["role"] == "textfield" and item.get("label") == "Address and search bar"), None)
    if not address:
        return None
    try:
        url = urlsplit(address if "://" in address else f"https://{address}")
        host = url.hostname or ""
    except ValueError:
        return None
    if GOOGLE_HOST.fullmatch(host) and url.path == "/search":
        query = parse_qs(url.query).get("q", [""])[0]
        if query:
            return ResultRequest(name, query, explicit)
    return None


def result_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def matching_search_results(request: ResultRequest, matches: list[dict]) -> list[tuple[dict, str]]:
    """Rank Google's web links and return the best match, or all equally good choices."""
    wanted = set(result_words(request.name))
    query = " ".join(result_words(request.query))
    ranked = []
    for item in matches:
        if item.get("role") != "link" or not item.get("snapshot_id") or "disabled" in item.get("states", []):
            continue
        label = item.get("full_label") or item.get("label", "")
        breadcrumb = RESULT_BREADCRUMB.search(label)
        if not breadcrumb:
            continue  # AI Overview and the knowledge panel expose links without the web result breadcrumb.
        title = label[:breadcrumb.start()].strip()
        title_words = result_words(title)
        site_words = set(result_words(breadcrumb[1]))
        if not title or not wanted or not wanted <= set(title_words) | site_words:
            continue
        score = (wanted <= site_words, wanted == set(title_words),
                 bool(query and f" {query} " in f" {' '.join(title_words)} "))
        ranked.append((score, item, title))
    best = max((score for score, _, _ in ranked), default=None)
    return [(item, title) for score, item, title in ranked if score == best]
