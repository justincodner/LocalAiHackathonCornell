"""Split "type X into the Y field" requests into simple steps, and run the simple ones in code.

"In the Amazon search bar search for Christmas gifts" is four steps:

    1. find the search bar       perception: code first, the model only if code can't tell
    2. type "Christmas gifts"     code
    3. press Return               code
    4. check the page changed     code (press the field's Search/Go button if Return did nothing)

Left to the model, the 2B typed the words and then called confirm seven times without ever
pressing Return, until the 45 s task timeout ("That took too long"). Only step 1 needs any
judgement, so only step 1 may reach the model, and with a one-tool prompt ("which field?").
"""

import asyncio
import re
import time
from dataclasses import dataclass

from . import browser, fastpath, risk, tools
from .helper_client import HelperError
from .llm import BonsaiError, ToolCall
from .log import append_step
from .page_summary import BROWSERS

TEXT_ROLES = {"textfield", "textarea", "combobox"}
FIELD_WORDS = r"(?:search\s+(?:bar|box|field)|searchbar|search|text\s+(?:box|field)|box|field|bar|input)"
SUBMIT_VERB = r"(?:search(?:\s+(?:for|up))?|look\s+up|find)"
TYPE_VERB = r"(?:type(?:\s+in)?|enter|write|put|fill\s+in|input)"
WHERE = (r"(?:in|into|inside|on|at|using|onto)\s+(?:the\s+|a\s+|this\s+|that\s+)?(?P<where>(?:[\w.'-]+\s+){0,3}?)"
         rf"(?P<kind>{FIELD_WORDS})")
# "In the Amazon search bar, search for Christmas gifts" / "in the search box type hello".
WHERE_FIRST = re.compile(rf"^{WHERE}\s*,?\s*(?:(?:and|then)\s+)?(?P<verb>{SUBMIT_VERB}|{TYPE_VERB})\s+(?P<text>.+)$")
# "Search for Christmas gifts in the Amazon search bar" / "type hello into the name field".
WHERE_LAST = re.compile(rf"^(?P<verb>{SUBMIT_VERB}|{TYPE_VERB})\s+(?P<text>.+?)\s*,?\s+{WHERE}$")
# "Type hello and press enter": the focused field.
TYPE_ENTER = re.compile(rf"^(?P<verb>{TYPE_VERB})\s+(?P<text>.+?)\s*,?\s+(?:and\s+|then\s+)+(?:press|hit|push)\s+"
                        r"(?:the\s+)?(?:enter|return)(?:\s+key)?$")
# "Search for invoices" with an app's own search field and no browser address bar to use.
BARE_SEARCH = re.compile(rf"^{SUBMIT_VERB}\s+(?P<text>.+)$")
AND_ENTER = re.compile(r"\s*,?\s+(?:and\s+|then\s+)+(?:press|hit|push)\s+(?:the\s+)?(?:enter|return)(?:\s+key)?$")
ON_THIS_PAGE = re.compile(r"\s+(?:on|in|from)\s+(?:this|the|my)\s+(?:page|site|website|app)$|\s+here$")
SUBMIT_BUTTONS = {"go", "search", "submit", "find", "search button", "submit search"}
ADDRESS_BAR = re.compile(r"\baddress\s+(?:and\s+search|bar)\b|\burl\b|^address$", re.IGNORECASE)  # the browser's own
STOP = set("the a an my this that of to for in on".split())
CHANGE_WAIT_S = 1.5  # a search page takes a moment to start loading
POLL_S = 0.25


@dataclass
class Request:
    text: str  # what to type
    where: str  # the words naming the field ("amazon search"), "" for any/focused
    submit: bool  # press Return after typing
    focused: bool = False  # type into whatever has focus ("type hello and press enter")


def parse(utterance: str) -> Request | None:
    """The steps for a type-into-a-field request, or None to leave it to the other paths."""
    text = browser._clean(utterance)
    if fastpath.wants_send(text) or browser.MORE_STEPS.search(AND_ENTER.sub("", text)):
        return None  # sends have their own path; "type X then click Y" is more than this
    # Dictation commas: "search for, Christmas gifts", "in the search bar, type, hello".
    text = re.sub(rf"\b({SUBMIT_VERB}|{TYPE_VERB})\s*,\s*", r"\1 ", text)
    enter = TYPE_ENTER.match(text)
    if enter and not re.search(WHERE + r"$", enter.group("text")):
        return Request(_strip_quotes(enter.group("text")), "", True, focused=True)
    pressed_enter = bool(AND_ENTER.search(text))
    text = AND_ENTER.sub("", text)
    match = WHERE_FIRST.match(text) or WHERE_LAST.match(text)
    if match:
        typed = match.group("text").strip(" ,")
        where = " ".join(match.group("where").split())
        if not typed or ADDRESS_BAR.search(f"{where} {match.group('kind')}") or typed in {"it", "that", "this", "something"}:
            return None
        submit = bool(re.match(SUBMIT_VERB, match.group("verb"))) or pressed_enter
        if re.search(r"search\w*\s*(?:bar|box|field)?$", match.group("kind")) and "search" not in where:
            where = f"{where} search".strip()  # "the amazon search bar" names a search field
        return Request(_strip_quotes(typed), where, submit)
    return None


def parse_bare_search(utterance: str) -> Request | None:
    """ "Search for invoices" outside a browser: into the app's own search field."""
    text = browser._clean(utterance)
    # "Look up Christmas gifts on this page/site": the page's own search field.
    text = re.sub(r"\s+(?:on|in|from)\s+(?:this|the|my)\s+(?:page|site|website|app)$|\s+here$", "", text)
    match = BARE_SEARCH.match(text)
    if not match or browser.ON_PAGE.search(text) or browser.MORE_STEPS.search(text):
        return None
    return Request(_strip_quotes(match.group("text").strip(" ,")), "search", True)


def _strip_quotes(text: str) -> str:
    return text.strip(" \"'“”‘’")


def _as_said(text: str, utterance: str) -> str:
    """The typed words in the case the user said them (parsing lowercases): "Christmas gifts"."""
    found = re.search(r"\s+".join(re.escape(word) for word in text.split()), utterance, re.IGNORECASE)
    return found.group(0) if found else text


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.casefold()) if word not in STOP}


def fields(snapshot: dict) -> list[dict]:
    """Enabled text fields, without the browser's own address bar."""
    found = []
    for item in snapshot.get("elements", []):
        if item.get("role") not in TEXT_ROLES or {"disabled", "inferred"} & set(item.get("states") or []):
            continue
        if snapshot.get("app", "").casefold() in BROWSERS and ADDRESS_BAR.search(item.get("label") or ""):
            continue
        found.append(item)
    return found


def find_field(snapshot: dict, where: str) -> tuple[dict | None, list[dict]]:
    """(the field the words name, the candidates) from labels alone: no model call.

    "amazon search" picks the field labelled "Search Amazon"; "search" picks the one field with
    "search" in its label; a page with a single text field needs no words at all.
    """
    candidates = fields(snapshot)
    if not candidates:
        return None, []
    wanted = _words(where)
    scored = []
    for item in candidates:
        label = _words(item.get("label") or "")
        score = len(wanted & label) + sum(0.5 for w in wanted for l in label if w != l and len(w) >= 4 and len(l) >= 4
                                          and (w in l or l in w or l.startswith(w[:4])))  # "message" ~ "iMessage"
        scored.append((score, item))
    best = max(score for score, _ in scored)
    top = [item for score, item in scored if score == best]
    if best > 0 and len(top) == 1:
        return top[0], candidates
    if len(candidates) == 1 and (best > 0 or not wanted or wanted <= {"search", "text", "box", "field"}):
        return candidates[0], candidates
    return None, candidates


def on_site(snapshot: dict, site: str) -> bool:
    """The page in front is that site (its title or address names it)."""
    window, address = page_key(snapshot)
    return site in (window or "").casefold() or site in (address or "").casefold()


def wants_submit(utterance: str) -> bool:
    """The request ends with a submit: a search, or "... and press enter"."""
    request = parse(utterance) or parse_bare_search(utterance)
    text = browser._clean(utterance)
    return bool(request and request.submit) or bool(re.match(SUBMIT_VERB + r"\s", text) or AND_ENTER.search(text))


def ranked(snapshot: dict, where: str, candidates: list[dict]) -> list[dict]:
    """Candidates sharing a word with the request first ("address": Email address, Street address)."""
    wanted = _words(where)
    return sorted(candidates, key=lambda item: -len(wanted & _words(item.get("label") or "")))


def page_key(snapshot: dict) -> tuple:
    """What a submitted search changes: the window title, the address, or the results."""
    address = next((item.get("value") for item in snapshot.get("elements", [])
                    if item.get("role") == "textfield" and ADDRESS_BAR.search(item.get("label") or "")), None)
    return snapshot.get("window"), address


def submit_button(snapshot: dict, field: dict) -> dict | None:
    """The Search/Go button that belongs to this field: the first such button after it."""
    ids = [item["id"] for item in snapshot["elements"]]
    start = ids.index(field["id"]) if field["id"] in ids else 0
    for item in snapshot["elements"][start:]:
        if (item.get("role") == "button" and "disabled" not in (item.get("states") or [])
                and fastpath.normalize(item.get("label") or "") in SUBMIT_BUTTONS):
            return item
    return None


async def ask_model_for_field(brain, snapshot: dict, request: Request, candidates: list[dict],
                              task_route: str) -> dict | None:
    """Step 1 when the labels don't say which field: the model picks one, with click as its only tool."""
    from .loop import FIND_FIELD_TOOLS
    from .memory import TaskMemory

    options = ", ".join(f'[{item["id"]}] {item.get("label") or "unlabelled"}' for item in candidates[:12])
    goal = (f"The user wants to type into the {request.where or 'text'} field. Which field is it? "
            f"The text fields are: {options}. Click the one that fits best. Do nothing else.")
    messages = TaskMemory(brain.prompt, goal).messages(snapshot)
    try:
        call, _ = await brain.think(messages, FIND_FIELD_TOOLS, filler=True, task_route=task_route)
    except BonsaiError:
        return None
    # Any call that names a text field answers "which field?" (the 2B sometimes types right away).
    element = tools.element_for(call, snapshot) if "id" in call.arguments else None
    return element if element is not None and element in candidates else None


async def run(brain, utterance: str, snapshot: dict, stt_ms: int, *, task_route: str = "single") -> bool:
    """Run a type-into-a-field request as steps; False leaves it to the full loop untouched."""
    request = parse(utterance)
    in_browser = snapshot["app"].casefold() in BROWSERS
    if request is None and (not in_browser or ON_THIS_PAGE.search(browser._clean(utterance))):
        # "Search for invoices" in Mail, "look up gifts on this page" in a browser (in a browser a
        # bare "search for X" is the address bar's, browser.py).
        request = parse_bare_search(utterance)
        if request is not None and find_field(snapshot, "search")[0] is None:
            return False  # no search field the words can name: the model reads the screen
    if request is None:
        return False
    request.text = _as_said(request.text, utterance)
    task_id = f"t_{brain.task_number}"
    step = 0

    def log(call: ToolCall, ok: bool, llm_ms: int = 0, act_ms: int = 0) -> None:
        nonlocal step
        step += 1
        append_step(brain.config.log_path, task_id=task_id, step=step, utterance=utterance, kind="steps",
                    route="steps", model=brain.last_answered_by if llm_ms else None,
                    snapshot_id=snapshot["snapshot_id"], snapshot_tokens=0, tool=call.name, args=call.arguments,
                    valid=True, risk="allow", ok=ok,
                    ms={"stt": stt_ms, "snapshot": snapshot.get("build_ms", 0), "llm": llm_ms, "act": act_ms})

    # Step 1: find the field (the only step that may need the model).
    field = None
    site = next((name for name in browser.SITE_SEARCH if name in _words(request.where)), None)
    if not request.focused:
        field, candidates = find_field(snapshot, request.where)
        if site and not on_site(snapshot, site):
            field, candidates = None, []  # "the Amazon search bar" on Google's page: not this field
        if field is None and candidates:
            asked = time.perf_counter()
            brain.last_answered_by = None
            field = await ask_model_for_field(brain, snapshot, request, ranked(snapshot, request.where, candidates), task_route)
            llm_ms = round((time.perf_counter() - asked) * 1000)
            if field is not None:
                log(ToolCall("steps-find", "click", {"id": field["id"]}), True, llm_ms=llm_ms)
        if field is None:
            if in_browser and site and request.submit:
                # "Search Christmas gifts in the Amazon search bar" with Amazon not open: its search page.
                plan = browser.plan(f"search for {request.text} on {site}", snapshot["app"])
                if plan is not None:
                    await brain.run_browser_plan(plan, utterance, snapshot, stt_ms)
                    return True
            if not candidates:
                return False  # nothing to type into here: the model may know a way
            await brain.say(f"I couldn't tell which {request.where or 'text'} field you mean.")
            brain.remember(utterance, "Couldn't find the field.", snapshot["app"])
            return True
    if field is not None and risk.check(ToolCall("steps-check", "type_text", {"text": request.text, "id": field["id"]}),
                                        snapshot) != "allow":
        return False  # a password field, Terminal...: the loop's own refusal speaks
    name = (" ".join(request.where.split()) + (" " if request.where else "")) if field is not None else ""
    caption = f"Searching for {request.text}" if request.submit else f"Typing {request.text}"
    highlight = {"highlight": tools.element_ref(snapshot, field["id"])} if field is not None else {}
    await brain.set_state("acting", caption=caption, **highlight)
    before = page_key(snapshot)
    try:
        # Step 2: type, replacing what the field held (Amazon keeps the last search).
        acted = time.perf_counter()
        if field is not None:
            await brain.helper.request("/ax/act", {**tools.element_ref(snapshot, field["id"]), "action": "focus"})
            if (field.get("value") or "").strip():
                await brain.helper.request("/input/key", {"keys": "cmd+a"})
        type_call = ToolCall("steps-type", "type_text", {"text": request.text, **({"id": field["id"]} if field else {})})
        await brain.helper.request("/input/type", {"text": request.text})
        log(type_call, True, act_ms=round((time.perf_counter() - acted) * 1000))
        if not request.submit:
            summary = f"I typed: {request.text.rstrip('.')}."
            await brain.say(summary)
            brain.remember(utterance, summary, snapshot["app"])
            return True
        # Step 3: Return. Code, always: this is the step the model kept failing to take.
        acted = time.perf_counter()
        await asyncio.sleep(0.1)  # let the page's suggestion list catch up with the typing
        await brain.helper.request("/input/key", {"keys": "return"})
        log(ToolCall("steps-enter", "press_key", {"keys": "return"}), True,
            act_ms=round((time.perf_counter() - acted) * 1000))
        # Step 4: check something changed; if not, press the field's own Search/Go button once.
        changed, after = await wait_for_change(brain, before)
        if not changed and field is not None and after is not None:
            button = submit_button(after, next((item for item in after["elements"] if item["id"] == field["id"]), field))
            if button is not None:
                await brain.helper.request("/ax/act", {**tools.element_ref(after, button["id"]), "action": "press"})
                log(ToolCall("steps-submit", "click", {"id": button["id"]}), True)
                changed, after = await wait_for_change(brain, before)
    except HelperError as failure:
        log(ToolCall("steps-failed", "press_key", {"keys": ""}), False)
        await brain.say("I couldn't type into that field. Please try again.")
        brain.remember(utterance, f"Failed: {failure.code}", snapshot["app"])
        return True
    where = f" in the {name.strip()} field" if name and "search" not in name else ""
    summary = (f"I searched for {request.text}{where}." if changed or not in_browser
               else f"I typed {request.text} and pressed Enter, but the page hasn't changed yet.")
    await brain.say(summary)
    brain.remember(utterance, summary, snapshot["app"])
    return True


async def wait_for_change(brain, before: tuple) -> tuple[bool, dict | None]:
    """Poll briefly for the title or address to change after a submit: (changed, latest screen)."""
    latest = None
    deadline = time.monotonic() + CHANGE_WAIT_S
    while True:
        await asyncio.sleep(POLL_S)
        try:
            latest = await brain.helper.snapshot()
        except HelperError:
            return False, latest
        if page_key(latest) != before:
            return True, latest
        if time.monotonic() >= deadline:
            return False, latest
