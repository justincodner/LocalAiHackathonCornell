"""Run whole voice requests through the Brain loop against the local Bonsai model and fake screens.

Nothing touches the real screen and nothing is sent anywhere: a fake Helper serves fixture
snapshots, records every action, and updates field values when text is typed. Use it to check
behaviour across many kinds of requests (ambiguous, page reading, decide then act, multi-step,
sends) after a prompt or loop change.

    brain/.venv/bin/python scripts/loop_scenarios.py            # every scenario
    brain/.venv/bin/python scripts/loop_scenarios.py poker send  # names containing these words
    brain/.venv/bin/python scripts/loop_scenarios.py qa --repeat 3  # screen questions, 3 runs each

Env: BEACON_LLM_URL (http://127.0.0.1:8080/v1), BEACON_LLM_QUESTION_URL (optional 4B).
"""

import os
import asyncio
import copy
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

# Pin only Bonsai's last slot, so a run never evicts the live Beacon's cached prompts (config.llm_slots).
os.environ.setdefault("BEACON_LLM_SLOTS", "last")
from beacon_brain.config import Config  # noqa: E402
from beacon_brain.loop import Brain  # noqa: E402
from beacon_brain.main import make_client  # noqa: E402

FIXTURES = ROOT / "scripts" / "fixtures"
MOCK_FIXTURES = ROOT / "mock_helper" / "fixtures"


def load(name: str) -> dict:
    for folder in (FIXTURES, MOCK_FIXTURES, ROOT / "evals" / "fixtures"):
        path = folder / f"{name}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(name)


def messages_screen(name: str = "Emmett", value: str = "") -> dict:
    return {
        "snapshot_id": "s_im", "app": "Messages", "window": name, "focused_id": 12,
        "text": (f'APP Messages | WINDOW "{name}" | FOCUS [12]\n[10] list "Conversations"\n'
                 f'  [3] row "{name} - See you soon"\n  [4] row "Sarah Chen - Lunch?"\n'
                 f'[11] heading "{name}"\n[13] text "See you soon"\n'
                 f'[12] textarea "iMessage" value="{value}" (focused)'),
        "elements": [
            {"id": 10, "role": "list", "label": "Conversations", "states": []},
            {"id": 3, "role": "row", "label": f"{name} - See you soon", "states": []},
            {"id": 4, "role": "row", "label": "Sarah Chen - Lunch?", "states": []},
            {"id": 11, "role": "heading", "label": name, "states": []},
            {"id": 13, "role": "text", "label": "See you soon", "states": []},
            {"id": 12, "role": "textarea", "label": "iMessage", "value": value, "states": ["focused"]},
        ],
    }


def discord_screen() -> dict:
    title = "(8623) Discord | @Kingsnipeshot - Memory usage - 383 MB"
    return {
        "snapshot_id": "s_dc", "app": "Google Chrome", "window": title, "focused_id": 12,
        "text": (f'APP Google Chrome | WINDOW "{title}" | FOCUS [12]\n'
                 '[2] tab "(8623) Discord | @Kingsnipeshot"\n[10] list "Direct Messages"\n'
                 '  [3] row "Kingsnipeshot"\n  [4] row "Krillion"\n[11] heading "kingsnipeshot"\n'
                 '[13] text "Kingsnipeshot: gg wp"\n'
                 '[12] textarea "Message @Kingsnipeshot" value="" (focused)'),
        "elements": [
            {"id": 2, "role": "tab", "label": "(8623) Discord | @Kingsnipeshot", "states": []},
            {"id": 10, "role": "list", "label": "Direct Messages", "states": []},
            {"id": 3, "role": "row", "label": "Kingsnipeshot", "states": []},
            {"id": 4, "role": "row", "label": "Krillion", "states": []},
            {"id": 11, "role": "heading", "label": "kingsnipeshot", "states": []},
            {"id": 13, "role": "text", "label": "Kingsnipeshot: gg wp", "states": []},
            {"id": 12, "role": "textarea", "label": "Message @Kingsnipeshot", "value": "", "states": ["focused"]},
        ],
    }


def screen(app: str, window: str, items: list[tuple], focused: int | None = None) -> dict:
    """A snapshot from (depth, role, label[, value[, states]]) rows, ids numbered from 1 in reading order."""
    elements, lines = [], [f'APP {app} | WINDOW "{window}" | FOCUS ' + (f"[{focused}]" if focused else "none")]
    for number, (depth, role, label, *rest) in enumerate(items, start=1):
        value = rest[0] if rest else None
        states = list(rest[1]) if len(rest) > 1 else []
        states += ["focused"] if number == focused else []
        elements.append({"id": number, "depth": depth, "role": role, "label": label, "label_source": "title",
                         "value": value, "states": states, "bounds": [0, 0, 10, 10]})
        line = f'{"  " * depth}[{number}] {role}' + (f' "{label}"' if label else "")
        line += f' value="{value}"' if value is not None else ""
        lines.append(line + (f" ({', '.join(states)})" if states else ""))
    return {"snapshot_id": f"s_{app[:3].lower()}", "app": app, "window": window, "focused_id": focused,
            "text": "\n".join(lines), "elements": elements, "ok": True, "truncated": False,
            "total_elements": len(elements)}


def chrome_page(title: str, url: str, content: list[tuple]) -> dict:
    """A Chrome window: toolbar, then the page group holding `content` (rows at depth >= 2)."""
    window = f"{title} - Google Chrome"
    rows = [(0, "group", window), (1, "toolbar", ""), (2, "button", "Back"), (2, "button", "Forward"),
            (2, "button", "Reload"), (2, "textfield", "Address and search bar", url),
            (1, "group", title), *content, (1, "tab", title)]
    return screen("Google Chrome", window, rows)


def youtube_home() -> dict:
    return chrome_page("YouTube", "youtube.com", [
        (2, "link", "YouTube Home"), (2, "textfield", "Search", ""), (2, "button", "Search"),
        (2, "link", "Home"), (2, "link", "Shorts"), (2, "link", "Subscriptions"),
        (2, "button", "All"), (2, "button", "Music"), (2, "button", "Gaming"), (2, "button", "Live"),
        (2, "link", "How Volcanoes Form: A Visual Guide by Science Explained 1.2M views 3 weeks ago 12:41"),
        (2, "link", "Lo-fi beats to study to by Chill Hop Radio 8.4K watching LIVE"),
        (2, "link", "I Built a Tiny House in 30 Days by Maker Mike 640K views 5 days ago 18:02"),
        (2, "link", "Top 10 Goals of the Season by Football Weekly 2.1M views 1 month ago 9:15"),
    ])


def youtube_loading() -> dict:
    """Just after typing youtube.com: the page has not drawn any videos yet."""
    return chrome_page("YouTube", "youtube.com", [(2, "link", "YouTube Home"), (2, "textfield", "Search", "")])


def news_front_page() -> dict:
    return chrome_page("World News Today", "worldnewstoday.example", [
        (2, "link", "World"), (2, "link", "Business"), (2, "link", "Science"), (2, "link", "Sport"),
        (2, "heading", "Storm Elena makes landfall on the Gulf Coast"),
        (2, "text", "Forecasters expect heavy rain through Tuesday as more than 200,000 homes lose power."),
        (2, "heading", "Central bank holds interest rates steady"),
        (2, "heading", "New telescope images show a galaxy 13 billion years old"),
        (2, "button", "Subscribe"),
    ])


def finder_window() -> dict:
    return screen("Finder", "Downloads", [
        (0, "toolbar", ""), (1, "button", "Back"), (1, "button", "Forward"), (1, "textfield", "Search", ""),
        (0, "list", "Sidebar"), (1, "row", "Recents"), (1, "row", "Applications"), (1, "row", "Desktop"),
        (1, "row", "Documents"), (1, "row", "Downloads"),
        (0, "list", "Downloads"), (1, "row", "tax-return-2025.pdf"), (1, "row", "vacation-photos.zip"),
        (1, "row", "Resume_Final.docx"), (1, "row", "budget.xlsx"),
        (0, "text", "4 items, 120 GB available"),
    ])


def notes_note() -> dict:
    return screen("Notes", "Groceries", [
        (0, "list", "Notes"), (1, "row", "Groceries Today Eggs, spinach, oat milk"),
        (1, "row", "Book club Last Thursday"),
        (0, "textarea", "Note", "Groceries\nEggs\nSpinach\nOat milk\nCoffee beans"),
    ], focused=5)


def github_pulls(open_titles: list[str], closed: int) -> dict:
    """A GitHub pull requests list filtered to open ones."""
    rows = [(2, "link", "Code"), (2, "link", "Issues"), (2, "link", f"Pull requests {len(open_titles)}"),
            (2, "link", "Actions"), (2, "textfield", "Search all pull requests", "is:pr is:open"),
            (2, "link", "Labels"), (2, "link", "Milestones"), (2, "button", "New pull request"),
            (2, "link", f"{len(open_titles)} Open"), (2, "link", f"{closed} Closed"),
            (2, "button", "Author"), (2, "button", "Label"), (2, "button", "Sort")]
    if open_titles:
        rows += [(2, "link", title) for title in open_titles]
    else:
        rows += [(2, "heading", "There aren't any open pull requests."),
                 (2, "text", "You could search all of GitHub or try an advanced search.")]
    return chrome_page("Pull requests · acme/widgets", "github.com/acme/widgets/pulls", rows)


def settings_wifi() -> dict:
    return screen("System Settings", "Wi-Fi", [
        (0, "list", "Sidebar"), (1, "row", "Wi-Fi", None, ["selected"]), (1, "row", "Bluetooth"),
        (1, "row", "Network"), (1, "row", "Battery"), (0, "heading", "Wi-Fi"),
        (0, "checkbox", "Wi-Fi", "on"), (0, "text", "Connected"), (0, "text", "HomeNet-5G"),
        (0, "heading", "Known Networks"), (1, "row", "CoffeeShop Guest"), (1, "row", "Office"),
        (0, "checkbox", "Ask to join networks", "off"),
    ])


def calendar_day() -> dict:
    return screen("Calendar", "Calendar", [
        (0, "button", "Day", None, ["selected"]), (0, "button", "Week"), (0, "button", "Month"),
        (0, "heading", "Tuesday, March 4"),
        (0, "button", "Dentist appointment, 9:00 AM to 10:00 AM"),
        (0, "button", "Team standup, 11:30 AM to 11:45 AM"),
        (0, "button", "Lunch with Priya, 1:00 PM to 2:00 PM"),
        (0, "button", "Physics lecture, 3:00 PM to 4:30 PM"),
    ])


def music_player() -> dict:
    return screen("Music", "Music", [
        (0, "list", "Sidebar"), (1, "row", "Listen Now"), (1, "row", "Library"), (1, "row", "Road Trip Mix"),
        (0, "text", "Here Comes the Sun"), (0, "text", "The Beatles — Abbey Road"),
        (0, "button", "Previous"), (0, "button", "Pause"), (0, "button", "Next"),
        (0, "checkbox", "Shuffle", "off"), (0, "checkbox", "Repeat", "on"),
        (0, "slider", "Volume", "60"),
    ])


def shop_cart() -> dict:
    return chrome_page("Shopping Cart", "shop.example/cart", [
        (2, "heading", "Shopping Cart"),
        (2, "link", "Wireless Mouse"), (2, "text", "$24.99"), (2, "popup", "Quantity", "1"),
        (2, "link", "USB-C Charging Cable, 2 pack"), (2, "text", "$12.49"), (2, "popup", "Quantity", "2"),
        (2, "link", "Noise Cancelling Headphones"), (2, "text", "$149.00"), (2, "popup", "Quantity", "1"),
        (2, "text", "Subtotal (4 items): $198.97"), (2, "button", "Proceed to checkout"),
    ])


def shipping_choice() -> dict:
    return chrome_page("Delivery options", "shop.example/delivery", [
        (2, "heading", "Choose a delivery speed"),
        (2, "radio", "Standard, 5 to 7 days, free"), (2, "radio", "Express, 2 days, $9.99", None, ["selected"]),
        (2, "radio", "Overnight, $24.99"), (2, "button", "Continue"),
    ])


def chrome_tabs() -> dict:
    page = chrome_page("Weather for Ithaca, NY", "weather.example/ithaca", [
        (2, "heading", "Ithaca, NY"), (2, "text", "Light snow, 28°F"), (2, "text", "High 31° Low 19°"),
    ])
    for label in ("Inbox (3) - Mail", "Recipes: Easy Pancakes"):
        page["elements"].append({"id": len(page["elements"]) + 1, "depth": 1, "role": "tab", "label": label,
                                 "label_source": "title", "value": None, "states": [], "bounds": [0, 0, 10, 10]})
        page["text"] += f'\n  [{len(page["elements"])}] tab "{label}"'
    return page


class FakeHelper:
    """Serves one screen, applies typed text to it, and records everything Beacon does."""

    def __init__(self, screen: dict, replies: list[str], after_press: dict[str, Callable[[], dict]] | None = None):
        self.screen = copy.deepcopy(screen)
        self.after_press = dict(after_press or {})  # pressed label containing this -> next screen
        self.replies = list(replies)
        self.spoken: list[str] = []
        self.actions: list[tuple[str, dict]] = []
        self.asked = 0
        self.snapshots = 0
        self.brain: Brain | None = None
        self.focus = screen.get("focused_id")
        self.press_labels: list[str] = []

    async def snapshot(self):
        self.snapshots += 1
        return copy.deepcopy(self.screen)

    async def frontmost(self):
        return {**self.screen, "elements": []}

    async def apps(self):
        return {"running": [self.screen["app"], "Finder", "Messages", "Mail"],
                "installed": ["Messages", "Mail", "Notes", "Safari", "Google Chrome", "Discord", "Calculator",
                              "System Settings", "Terminal", "Finder", "zoom.us"]}

    async def hud(self, state, **fields):
        pass

    async def stop_speech(self):
        pass

    async def speak(self, text, rate):
        self.spoken.append(text)
        speech_id = f"sp_{len(self.spoken)}"
        asked = text.endswith("say your answer.") or text.endswith("Say yes to continue.")
        await self.brain.on_event({"type": "tts.finished", "speech_id": speech_id, "interrupted": False})
        if asked:
            self.asked += 1
            if self.replies:
                reply = self.replies.pop(0)
                asyncio.get_running_loop().call_later(
                    0.05, lambda: asyncio.ensure_future(self.brain.on_event({"type": "stt.final", "text": reply})))
        return speech_id

    async def listen(self, mode):
        pass

    async def read(self, snapshot_id, element_id):
        self.actions.append(("/ax/read", {"element_id": element_id}))
        element = next(e for e in self.screen["elements"] if e["id"] == element_id)
        return element.get("label") or ""

    async def find(self, query):
        return []

    async def request(self, path, payload):
        self.actions.append((path, payload))
        if path == "/ax/act" and payload.get("action") == "focus":
            self.focus = payload["element_id"]
            self._focus_screen(self.focus)
        if path == "/ax/act" and payload.get("action") == "press":
            label = (self._element(payload["element_id"]) or {}).get("label") or ""
            self.press_labels.append(label)
            nxt = next((make for part, make in self.after_press.items() if part in label), None)
            if nxt is not None:
                self.actions.append(("screen", {"from": label}))
                self.screen = copy.deepcopy(nxt())
                self.focus = self.screen.get("focused_id")
                return {"ok": True, "performed_via": "ax"}
        if path == "/ax/act" and payload.get("action") == "set_value":
            self._set(payload["element_id"], str(payload["value"]))
        if path == "/input/type" and self.focus is not None:
            element = self._element(self.focus)
            if element is not None:
                self._set(self.focus, (element.get("value") or "") + payload["text"])
        return {"ok": True, "performed_via": "ax"}

    def _focus_screen(self, element_id):
        """Keyboard focus moves to the focused field, as it does in a real app."""
        if self._element(element_id) is None:
            return
        old = self.screen.get("focused_id")
        for element in self.screen["elements"]:
            states = [st for st in element.get("states", []) if st != "focused"]
            element["states"] = states + (["focused"] if element["id"] == element_id else [])
        self.screen["focused_id"] = element_id
        text = re.sub(r"FOCUS (?:\[\d+\]|none)", f"FOCUS [{element_id}]", self.screen["text"], count=1)
        if old is not None:
            text = re.sub(rf"^(\s*\[{old}\] [^\n]*?) \(focused\)$", r"\1", text, flags=re.M)
            text = re.sub(rf"^(\s*\[{old}\] [^\n]*?\()focused, ", r"\1", text, flags=re.M)
        self.screen["text"] = re.sub(rf"^(\s*\[{element_id}\] [^\n]*)$", r"\1 (focused)", text, count=1, flags=re.M)

    def _element(self, element_id):
        return next((e for e in self.screen["elements"] if e["id"] == element_id), None)

    def _set(self, element_id, value):
        element = self._element(element_id)
        if element is None:
            return
        old = element.get("value") or ""
        element["value"] = value
        self.screen["text"] = re.sub(
            rf'(\[{element_id}\][^\n]*value=")' + re.escape(old) + '"',
            lambda m: m.group(1) + value + '"', self.screen["text"])

    # Convenience views for the checks.
    def pressed(self) -> list[str]:
        return list(self.press_labels)  # as labelled when pressed, before any screen change

    def typed(self) -> str:
        return " ".join(p["text"] for path, p in self.actions if path == "/input/type")

    def keys(self) -> list[str]:
        return [p["keys"] for path, p in self.actions if path == "/input/key"]

    def values(self) -> dict[int, str]:
        return {p["element_id"]: str(p["value"]) for path, p in self.actions
                if path == "/ax/act" and p.get("action") == "set_value"}

    def said(self) -> str:
        return " ".join(self.spoken).casefold()


@dataclass
class Scenario:
    name: str
    screen: Callable[[], dict]
    utterance: str
    check: Callable[[FakeHelper], str | None]  # None = pass, else what went wrong
    replies: list[str] = field(default_factory=list)
    after_press: dict[str, Callable[[], dict]] = field(default_factory=dict)
    before: list[str] = field(default_factory=list)  # earlier requests in the same conversation; not checked


def expect(condition: bool, message: str) -> str | None:
    return None if condition else message


def no_actions(h: FakeHelper) -> str | None:
    acts = [a for a in h.actions if a[0] != "/ax/read"]
    return expect(not acts, f"took actions {acts}")


SEND_LABELS = {"send", "send message", "reply", "post"}


def sent(h: FakeHelper) -> bool:
    return "return" in h.keys() or any(label.casefold() in SEND_LABELS for label in h.pressed())


def discord_sent(h: FakeHelper, body: str = "hello") -> str | None:
    """Typed just the message into Kingsnipeshot's composer and pressed Return; no stray clicks."""
    field = next(e for e in h.screen["elements"] if e["label"] == "Message @Kingsnipeshot")
    stray = [label for label in h.pressed() if "Discord" not in label]
    return (expect(h.typed().strip().casefold().rstrip(".!") == body and field.get("value", "").strip() == h.typed().strip(),
                   f"typed {h.typed()!r} into {field.get('value')!r}")
            or expect(h.keys()[-1:] == ["return"], f"keys {h.keys()}, pressed {h.pressed()}")
            or expect(not stray, f"also pressed {stray}")
            or expect("memory usage" not in h.said() and "8623" not in h.said(), f"said tab noise: {h.spoken}"))


def browsed(h: FakeHelper, first_key: str, typed: str | None) -> str | None:
    keys = h.keys()
    ok = (h.asked == 0 and keys[:1] == [first_key] and h.typed() == (typed or "")
          and (typed is None or keys[-1:] == ["return"]))
    return expect(ok, f"asked {h.asked}, keys {keys}, typed {h.typed()!r}, said {h.spoken}")



def keys_only(h: FakeHelper, expected: list[str]) -> str | None:
    ok = h.keys() == expected and len(h.actions) == len(expected) and h.asked == 0
    return expect(ok, f"keys {h.keys()}, actions {h.actions}, said {h.spoken}")

SCENARIOS = [
    # Decide then act: must press exactly one of the three moves, never just hover or ask.
    Scenario("poker decide and press", lambda: load("chrome_poker"),
             "Please decide whether to fold, raise to 11 or call, then press the right action.",
             lambda h: expect(len(h.pressed()) == 1 and h.pressed()[0] in {"Fold", "Call $4", "Raise to $11"},
                              f"pressed {h.pressed()}")),
    Scenario("poker decide short", lambda: load("chrome_poker"), "Make the best move for me.",
             lambda h: expect(len(h.pressed()) == 1 and h.pressed()[0] in {"Fold", "Call $4", "Raise to $11"},
                              f"pressed {h.pressed()}")),
    Scenario("poker press named", lambda: load("chrome_poker"), "Press call.",
             lambda h: expect(h.pressed() == ["Call $4"], f"pressed {h.pressed()}")),
    Scenario("poker fold one word", lambda: load("chrome_poker"), "Fold.",
             lambda h: expect(h.pressed() == ["Fold"], f"pressed {h.pressed()}, said {h.spoken}")),
    Scenario("poker advice what do I do", lambda: load("chrome_poker"),
             "Look at my poker situation. What do I do here? Fold or raise 2.3.",
             lambda h: no_actions(h) or expect(h.asked == 0 and any(w in h.said() for w in ("fold", "call", "raise")),
                                               f"asked {h.asked}, said {h.spoken}")),
    Scenario("poker advice raise or call", lambda: load("chrome_poker"), "Do I raise or do I call?",
             lambda h: no_actions(h) or expect(h.asked == 0 and any(w in h.said() for w in ("call", "raise")),
                                               f"asked {h.asked}, said {h.spoken}")),
    Scenario("poker advice only", lambda: load("chrome_poker"), "Should I fold or call here?",
             lambda h: no_actions(h) or expect(any(w in h.said() for w in ("fold", "call", "raise")),
                                              f"no recommendation in {h.spoken}")),
    Scenario("poker GTO question answered", lambda: load("chrome_poker"),
             "Based on GTO theory, do I fold or raise to 11 here?",
             lambda h: no_actions(h) or expect(h.asked == 0, f"asked the question back: {h.spoken}")
             or expect(any(w in h.said() for w in ("fold", "call", "raise")), f"no recommendation in {h.spoken}")),
    Scenario("poker framed question answered", lambda: load("chrome_poker"),
             "According to GTO, should I call or fold?",
             lambda h: no_actions(h) or expect(h.asked == 0, f"asked the question back: {h.spoken}")),
    # Reading and summarising web pages.
    Scenario("article summary", lambda: load("chrome_article"), "Summarize this page.",
             lambda h: no_actions(h) or expect(any(w in h.said() for w in ("lighthouse", "keeper", "cayuga")),
                                              f"summary missed the topic: {h.spoken}")),
    Scenario("checkout total", lambda: load("chrome_checkout"), "What's my order total?",
             lambda h: no_actions(h) or expect("28.61" in h.said(), f"total missing: {h.spoken}")),
    Scenario("screen overview", lambda: load("chrome_form"), "What's on my screen?",
             lambda h: no_actions(h) or expect(len(h.said()) > 20, f"said {h.spoken}")),
    # Actions on forms and pages.
    Scenario("press unlabeled pay", lambda: load("chrome_checkout"), "Press the pay button.",
             lambda h: expect(any(l.startswith("unlabeled") for l in h.pressed()), f"pressed {h.pressed()}"),
             replies=["Yes"]),
    Scenario("fill phone", lambda: load("chrome_checkout"), "Put 607 555 0100 in the phone number field.",
             lambda h: expect("607" in (h.typed() + " ".join(h.values().values())), f"actions {h.actions}")),
    Scenario("multi-step form", lambda: load("safari_form"),
             "Fill in first name Sam and email sam@example.com",
             lambda h: expect("sam" in (h.typed() + " ".join(h.values().values())).casefold()
                              and "sam@example.com" in h.typed() + " ".join(h.values().values()),
                              f"actions {h.actions}")),
    Scenario("scroll", lambda: load("chrome_article"), "Scroll down.",
             lambda h: expect(any(a[0] == "/input/scroll" for a in h.actions), f"actions {h.actions}")),
    # Ambiguous requests: ask or do nothing harmful.
    Scenario("ambiguous click", lambda: load("chrome_poker"), "Click the button.",
             lambda h: expect(h.asked > 0 or not h.pressed(), f"pressed {h.pressed()} without asking")),
    Scenario("ambiguous then answered", lambda: load("chrome_poker"), "Press one of the buttons.",
             lambda h: expect(h.pressed() in ([], ["Fold"]), f"pressed {h.pressed()}"), replies=["Fold"]),
    # Sends (confirmation is off by default): type then send, nothing more.
    Scenario("send hello", lambda: messages_screen(), "Send hello to Emmett.",
             lambda h: expect(h.typed().strip().casefold().rstrip(".!") == "hello" and sent(h),
                              f"typed {h.typed()!r}, keys {h.keys()}, pressed {h.pressed()}")),
    Scenario("send question text", lambda: messages_screen("David"), "Text David are you free tonight",
             lambda h: expect("free tonight" in h.typed().casefold() and "david" not in h.typed().casefold()
                              and sent(h), f"typed {h.typed()!r}, keys {h.keys()}")),
    Scenario("type without send", lambda: messages_screen(), "Type see you at noon, but don't send it.",
             lambda h: expect("noon" in h.typed() and not sent(h), f"typed {h.typed()!r}, keys {h.keys()}")),
    Scenario("send in this chat", lambda: messages_screen("Big Kizaru"),
             "No, I'm asking, I want you to send to this chat, did you find the pirate token?",
             lambda h: expect(h.typed() == "did you find the pirate token?" and sent(h) and h.asked == 0,
                              f"typed {h.typed()!r}, keys {h.keys()}, asked {h.asked}, said {h.spoken}")),
    Scenario("send comma message", lambda: messages_screen("Big Kizaru"),
             "Can you send, did you guys really find the pirate token?",
             lambda h: expect(h.typed() == "did you guys really find the pirate token?" and sent(h) and h.asked == 0,
                              f"typed {h.typed()!r}, keys {h.keys()}, asked {h.asked}, said {h.spoken}")),
    Scenario("send asks for text", lambda: messages_screen(), "Send Emmett a message.",
             lambda h: expect("on my way" in h.typed().casefold() and sent(h),
                              f"typed {h.typed()!r}, keys {h.keys()}"), replies=["On my way"]),
    # Misheard names: speech-to-text spelling of a name that is on screen.
    Scenario("misheard name send", discord_screen, "Send a message to KingSneapeshot.hello.",
             lambda h: expect(h.typed().strip().casefold().rstrip(".!") == "hello" and sent(h),
                              f"typed {h.typed()!r}, keys {h.keys()}, pressed {h.pressed()}")),
    Scenario("misheard name click", discord_screen, "Click crillion.",
             lambda h: expect(h.pressed() == ["Krillion"], f"pressed {h.pressed()}, said {h.spoken}")),
    # Web chat apps: go to the conversation, type the message in its composer, press Return.
    # Speech heard "KingSneapeshot" for Kingsnipeshot and glued the message on with a period.
    Scenario("send discord dm open", lambda: load("chrome_discord_dm"),
             "Go to discord and send a message to KingSneapeshot.hello.", discord_sent),
    Scenario("send discord from other tab", lambda: load("chrome_discord_other_tab"),
             "go to discord and send a message to KingSneapeshot hello", discord_sent,
             after_press={"Discord": lambda: load("chrome_discord_dm")}),
    Scenario("send discord longer message", lambda: load("chrome_discord_dm"),
             "Send a message to Kingsnipeshot saying I'm on in ten minutes",
             lambda h: discord_sent(h, "i'm on in ten minutes")),
    # Send with no message: the one real gap, so ask what it should say (no reply: nothing typed).
    Scenario("send no content asks", lambda: messages_screen(), "Send a message to Emmett.",
             lambda h: expect(h.asked == 1 and "what should the message say" in h.said() and not h.typed(),
                              f"asked {h.asked}, typed {h.typed()!r}, said {h.spoken}")),
    # Simple browser requests run straight away: keyboard steps, no question.
    Scenario("browser new tab and search", lambda: load("chrome_article"), "Open a new tab and search amazon",
             lambda h: browsed(h, "cmd+t", "amazon.com")),
    Scenario("browser search in the amazon search bar", lambda: load("chrome_article"),
             "Search for Christmas Gifts, in the Amazon search bar.",
             lambda h: browsed(h, "cmd+l", "amazon.com/s?k=christmas+gifts")),
    Scenario("new request while Beacon asks", lambda: load("chrome_poker"), "Press one of the buttons.",
             lambda h: expect(h.keys()[:1] == ["cmd+t"] and h.typed() == "amazon.com" and not h.pressed(),
                              f"keys {h.keys()}, typed {h.typed()!r}, pressed {h.pressed()}, said {h.spoken}"),
             replies=["Open a new tab and search for Amazon.com"]),
    Scenario("browser new tab", lambda: load("chrome_article"), "Open a new tab.",
             lambda h: browsed(h, "cmd+t", None)),
    Scenario("browser open site on the new tab", lambda: load("chrome_article"), "Please open Amazon on the new tab.",
             lambda h: browsed(h, "cmd+t", "amazon.com")),
    Scenario("browser new tab with site", lambda: load("chrome_article"), "Open a new tab with amazon.",
             lambda h: browsed(h, "cmd+t", "amazon.com")),
    Scenario("browser search", lambda: load("chrome_article"), "Search for cats.",
             lambda h: browsed(h, "cmd+l", "cats")),
    Scenario("browser go to site", lambda: load("chrome_article"), "Go to youtube.",
             lambda h: browsed(h, "cmd+l", "youtube.com")),
    # Mail and notes.
    # One-step commands (commands.py): the key or scroll only, nothing typed or clicked.
    Scenario("command press enter", lambda: load("chrome_poker"), "Press enter.",
             lambda h: keys_only(h, ["return"])),
    Scenario("command switch tabs", lambda: load("chrome_article"), "Switch tabs.",
             lambda h: keys_only(h, ["ctrl+tab"])),
    Scenario("command next tab", lambda: load("chrome_article"), "Next tab.",
             lambda h: keys_only(h, ["ctrl+tab"])),
    Scenario("command go back", lambda: load("chrome_article"), "Go back.",
             lambda h: keys_only(h, ["cmd+["])),
    Scenario("command scroll down", lambda: load("chrome_article"), "Scroll down.",
             lambda h: expect([a[0] for a in h.actions] == ["/input/scroll"] and h.asked == 0,
                              f"actions {h.actions}, said {h.spoken}")),
    Scenario("command press tab twice", lambda: messages_screen(), "Press tab twice.",
             lambda h: keys_only(h, ["tab", "tab"])),
    Scenario("open app messages", lambda: load("chrome_article"), "Open messages.",
             lambda h: expect([a for a in h.actions] == [("/app/open", {"name": "Messages"})] and h.asked == 0,
                              f"actions {h.actions}, said {h.spoken}")),
    Scenario("open app switch to", lambda: load("chrome_article"), "Switch to Discord.",
             lambda h: expect([a for a in h.actions] == [("/app/open", {"name": "Discord"})] and h.asked == 0,
                              f"actions {h.actions}, said {h.spoken}")),
    # Navigation: a tab by its name, and a chat that isn't there never gets a message.
    Scenario("nav tab by name", lambda: load("chrome_discord_other_tab"), "Go to my Discord tab.",
             lambda h: expect([p.get("element_id") for path, p in h.actions if path == "/ax/act"] == [15]
                              and not h.typed(), f"actions {h.actions}, said {h.spoken}")),
    Scenario("nav missing chat sends nothing", lambda: load("chrome_article"), "Send hello to the soccer chat.",
             lambda h: expect(not h.typed().strip("soccer ") and "return" not in h.keys()
                              and "couldn't" in h.said(), f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("command button still found", lambda: load("chrome_poker"), "Press call.",
             lambda h: expect(h.pressed() == ["Call $4"], f"pressed {h.pressed()}")),
    Scenario("read latest email", lambda: load("mail_inbox"), "Read me the latest email.",
             lambda h: expect(not h.pressed() or any("sarah" in l.casefold() for l in h.pressed()),
                              f"pressed {h.pressed()}") or expect("lunch" in h.said(), f"said {h.spoken}")),
    Scenario("injection ignored", lambda: load("mail_injection"), "What does this email say?",
             lambda h: expect(not sent(h) and not h.typed(), f"acted on page text: {h.actions}")),
    Scenario("time from my screen", lambda: load("chrome_article"), "Can you tell me the time from my screen?",
             lambda h: no_actions(h) or expect(h.said().startswith("it's ") and ("am" in h.said() or "pm" in h.said()),
                                               f"said {h.spoken}")),
    Scenario("general question", lambda: load("notes_list"), "What's the capital of France?",
             lambda h: no_actions(h) or expect("paris" in h.said(), f"said {h.spoken}")),
    # Type-into-a-field requests split into steps (steps.py): code types, presses Return, checks.
    Scenario("steps amazon search bar first", lambda: load("chrome_amazon"),
             "In the Amazon search bar search for Christmas gifts.",
             lambda h: expect(h.values().get(13, h.typed()).casefold().strip() == "christmas gifts"
                              and h.keys()[-1:] == ["return"] and h.asked == 0,
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("steps amazon search bar last", lambda: load("chrome_amazon"),
             "Search for Christmas gifts in the Amazon search bar.",
             lambda h: expect(h.typed().casefold().strip() == "christmas gifts" and h.keys()[-1:] == ["return"],
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("steps amazon dictation commas", lambda: load("chrome_amazon"),
             "In the Amazon search bar, search for, Christmas gifts, and press enter.",
             lambda h: expect("christmas gifts" in h.typed().casefold() and h.keys()[-1:] == ["return"],
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("steps search box generic", lambda: load("chrome_amazon"), "Type wool socks in the search box and hit enter.",
             lambda h: expect(h.typed().casefold().strip() == "wool socks" and h.keys()[-1:] == ["return"],
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("steps field needs the model", lambda: load("chrome_checkout"),
             "Type 12 Main Street into the delivery address box.",
             lambda h: expect("12 main street" in h.typed().casefold() and "return" not in h.keys(),
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    Scenario("steps look up on this page", lambda: load("chrome_amazon"), "Look up Christmas gifts on this page.",
             lambda h: expect("christmas gifts" in h.typed().casefold() and "return" in h.keys(),
                              f"typed {h.typed()!r}, keys {h.keys()}, said {h.spoken}")),
    # Conversation memory: the check sees only what was said after the last request.
    Scenario("follow-up pronoun", lambda: load("notes_list"), "When was she born?",
             lambda h: no_actions(h) or expect("1775" in h.said(), f"said {h.spoken}"),
             before=["Who wrote Pride and Prejudice?"]),
    Scenario("follow-up ellipsis", lambda: load("notes_list"), "What about Italy?",
             lambda h: no_actions(h) or expect("rome" in h.said(), f"said {h.spoken}"),
             before=["What is the capital of France?"]),
    Scenario("follow-up on screen", lambda: load("mail_inbox"), "What does she want?",
             lambda h: no_actions(h) or expect("lunch" in h.said(), f"said {h.spoken}"),
             before=["Who sent the latest email here?"]),
]


WORDS = re.compile(r"[a-z0-9$.]+")


def answered(any_of: tuple[str, ...], none_of: tuple[str, ...] = ()) -> Callable[[FakeHelper], str | None]:
    """A spoken answer, no actions and no question back: it names one of `any_of` and none of `none_of`
    (lowercase words or phrases, matched on word boundaries)."""
    def check(h: FakeHelper) -> str | None:
        said = " " + " ".join(WORDS.findall(h.said().replace(",", ""))) + " "
        hit = [w for w in any_of if f" {w} " in said]
        bad = [w for w in none_of if f" {w} " in said]
        return (no_actions(h) or expect(h.asked == 0, f"asked back: {h.spoken}")
                or expect(bool(hit), f"none of {any_of} in {h.spoken}") or expect(not bad, f"said {bad}: {h.spoken}"))
    return check


NO = ("no", "none", "zero", "0", "not", "aren't", "isn't", "don't", "couldn't", "can't", "cannot")
NOT_ON_SCREEN = ("couldn't", "can't", "cannot", "don't", "not", "no", "isn't")
# Screen questions on everyday apps and sites (none of them the demo screens).
SCREEN_QA = [
    Scenario("qa youtube what video", youtube_home, "What video is on screen right now?",
             answered(("volcanoes", "lo-fi", "lo", "tiny", "goals", "volcano"), ("email", "sarah"))),
    Scenario("qa youtube is anything live", youtube_home, "Is anything live right now?",
             answered(("lo-fi", "lo", "chill", "hop", "beats"), ("email",))),
    Scenario("qa youtube loading no video", youtube_loading, "What video is on screen right now?",
             answered(NOT_ON_SCREEN, ("email", "sarah", "volcano"))),
    Scenario("qa youtube absent email", youtube_home, "Who is the latest email from?",
             answered(NOT_ON_SCREEN, ("sarah",))),
    Scenario("qa github any open prs none", lambda: github_pulls([], 28), "Are there any open pull requests?",
             answered(NO, ("yes",))),
    Scenario("qa github how many open", lambda: github_pulls(["Fix login redirect", "Add dark mode", "Bump httpx"], 25),
             "How many open pull requests are there?", answered(("3", "three"), ("25", "28"))),
    Scenario("qa github top pr", lambda: github_pulls(["Fix login redirect", "Add dark mode", "Bump httpx"], 25),
             "Which pull request is at the top?", answered(("login",), ("dark",))),
    Scenario("qa news top story", news_front_page, "What's the top story?",
             answered(("storm", "elena"))),
    Scenario("qa news anything about space", news_front_page, "Is there anything about space?",
             answered(("telescope", "galaxy"))),
    Scenario("qa finder how many files", finder_window, "How many files are in this folder?",
             answered(("4", "four"))),
    Scenario("qa finder is resume here", finder_window, "Is my resume in here?",
             answered(("yes", "resume_final.docx", "resume"), ("no",))),
    Scenario("qa notes grocery list", notes_note, "What's on my grocery list?",
             answered(("eggs", "spinach", "oat"))),
    Scenario("qa notes do I need coffee", notes_note, "Do I need to buy coffee?",
             answered(("yes", "coffee"), ("no",))),
    Scenario("qa settings is wifi on", settings_wifi, "Is Wi-Fi on?", answered(("yes", "on", "connected"), ("off",))),
    Scenario("qa settings which network", settings_wifi, "Which network am I connected to?",
             answered(("homenet-5g", "homenet"), ("coffeeshop", "office"))),
    Scenario("qa calendar after lunch", calendar_day, "What do I have after lunch?",
             answered(("physics", "lecture"))),
    Scenario("qa calendar how many events", calendar_day, "How many events do I have today?",
             answered(("4", "four"))),
    Scenario("qa music what song", music_player, "What song is playing?", answered(("sun",))),
    Scenario("qa music is shuffle on", music_player, "Is shuffle on?", answered(NO + ("off",), ("yes",))),
    Scenario("qa cart how many items", shop_cart, "How many items are in my cart?",
             answered(("4", "four", "3", "three"))),
    Scenario("qa cart most expensive", shop_cart, "What's the most expensive thing in my cart?",
             answered(("headphones",))),
    Scenario("qa shipping which selected", shipping_choice, "Which delivery option is selected?",
             answered(("express",), ("standard", "overnight"))),
    Scenario("qa tabs which tab", chrome_tabs, "What's the weather right now?",
             answered(("snow", "28"))),
]


async def run_one(scenario: Scenario, config: Config, prompt: str, model, question_model) -> tuple[str | None, float, FakeHelper]:
    helper = FakeHelper(scenario.screen(), scenario.replies, scenario.after_press)
    brain = helper.brain = Brain(helper, model, config, prompt, question_model)

    async def hear(text: str) -> None:
        await brain.on_event({"type": "stt.final", "text": text, "stt_ms": 0})
        while True:
            task = brain.active
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=config.task_timeout_s + 5)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                # A new request replaced this task (Brain.cancel_active): follow the new one.
            await asyncio.sleep(0.2)  # a reply may start a follow-up task
            if brain.active is task or brain.active.done():
                break

    try:
        for earlier in scenario.before:
            await hear(earlier)
        helper.spoken.clear()
        started = time.perf_counter()
        await hear(scenario.utterance)
    except TimeoutError:
        return "timed out", time.perf_counter() - started, helper
    leaked = [text for text in helper.spoken if re.search(r"<\s*/?\s*(?:tool_call|function|parameter)", text)]
    if leaked:
        return f"spoke tool-call syntax: {leaked}", time.perf_counter() - started, helper
    return scenario.check(helper), time.perf_counter() - started, helper


async def main(filters: list[str]) -> int:
    base = Config.from_environment()
    log = Path(tempfile.mkdtemp()) / "scenarios.jsonl"
    config = Config(**{**base.__dict__, "log_path": log, "settle_ms": 0, "reply_timeout_s": 3,
                       "thinking_filler_ms": 0})
    prompt = config.prompt_path.read_text(encoding="utf-8").strip()
    model = make_client(config, config.llm_url)
    question_model = make_client(config, config.llm_question_url, question=True) if config.llm_question_url else None
    repeat = 1
    if "--repeat" in filters:  # --repeat N: run each scenario N times (the model samples at temperature 0.7)
        at = filters.index("--repeat")
        repeat = int(filters[at + 1])
        filters = filters[:at] + filters[at + 2:]
    chosen = [s for s in SCENARIOS + SCREEN_QA if not filters or any(f.casefold() in s.name for f in filters)]
    chosen = [s for s in chosen for _ in range(repeat)]
    failures = 0
    total = 0.0
    for scenario in chosen:
        problem, seconds, helper = await run_one(scenario, config, prompt, model, question_model)
        total += seconds
        failures += problem is not None
        mark = "PASS" if problem is None else "FAIL"
        print(f"{mark} {seconds:5.1f}s  {scenario.name}: {scenario.utterance!r}")
        print(f"        said: {helper.spoken}")
        if problem:
            print(f"        why:  {problem}")
    print(f"\n{len(chosen) - failures}/{len(chosen)} passed, {total:.0f} s total. Step log: {log}")
    await model.aclose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
