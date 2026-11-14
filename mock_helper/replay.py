"""Replay saved screen snapshots without touching the operating system."""

import itertools
import json
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "contracts" / "fixtures"
SYNTHETIC_FIXTURES = Path(__file__).resolve().parent / "fixtures"
EDITABLE = {"textfield", "textarea"}
PRESSABLE = {"button", "link", "checkbox", "radio", "menuitem", "row", "tab", "popup"}


class HelperError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.error = {"code": code, "message": message, **details}


def render_screen(snapshot: dict) -> str:
    focus = snapshot["focused_id"]
    lines = [f'APP {snapshot["app"]} | WINDOW {json.dumps(snapshot["window"], ensure_ascii=False)}']
    if focus is not None:
        lines[0] += f" | FOCUS [{focus}]"
    for item in snapshot["elements"]:
        label = json.dumps(item["label"], ensure_ascii=False)
        line = f'{"  " * item["depth"]}[{item["id"]}] {item["role"]} {label}'
        if item["value"] is not None:
            line += f' value={json.dumps(item["value"], ensure_ascii=False)}'
        if item["states"]:
            line += f' ({", ".join(item["states"])})'
        lines.append(line)
    return "\n".join(lines)


class Replay:
    def __init__(self, fixtures_dir: Path, initial_fixture: str) -> None:
        self.paths = {path.stem: path for path in SYNTHETIC_FIXTURES.glob("*.json")}
        self.paths.update({path.stem: path for path in fixtures_dir.glob("*.json")})
        self.snapshot_ids = itertools.count(1)
        self.history: OrderedDict[str, dict[int, dict]] = OrderedDict()
        self.name = ""
        self.screen: dict = {}
        self.select(initial_fixture)

    def select(self, name: str) -> bool:
        path = self.paths.get(name)
        if path is None:
            raise ValueError(f"Unknown fixture {name!r}; available: {', '.join(sorted(self.paths))}")
        screen = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(screen, dict) or not {
            "ok", "app", "window", "focused_id", "truncated", "total_elements",
            "build_ms", "elements", "text", "snapshot_id",
        }.issubset(screen) or not isinstance(screen["elements"], list):
            raise ValueError(f"{path.name} does not contain a Beacon screen snapshot")
        changed_window = (self.screen.get("app"), self.screen.get("window")) != (
            screen["app"], screen["window"]
        )
        self.history.clear()
        self.screen, self.name = screen, name
        return changed_window

    def snapshot(self, scope: str, limit: int) -> dict:
        snapshot = deepcopy(self.screen)
        if scope == "focused":
            focus = self.screen["focused_id"]
            items, _ = self.subtree(focus)
        elif scope == "menu":
            items = [item for item in snapshot["elements"] if item["role"] in {"menu", "menuitem"}]
        else:
            items = snapshot["elements"]
        snapshot["elements"] = items[:limit]
        if scope != "window":
            snapshot["total_elements"] = len(items)
        snapshot["truncated"] = snapshot["truncated"] or len(items) > limit
        if not any(item["id"] == snapshot["focused_id"] for item in snapshot["elements"]):
            snapshot["focused_id"] = None
        if scope != "window" or len(items) > limit:
            snapshot["text"] = render_screen(snapshot)
        snapshot["snapshot_id"] = self.remember(snapshot["elements"])
        return snapshot

    def remember(self, items: list[dict]) -> str:
        snapshot_id = f"s_{next(self.snapshot_ids):04d}"
        self.history[snapshot_id] = {item["id"]: item for item in items}
        if len(self.history) > 3:
            self.history.popitem(last=False)
        return snapshot_id

    def find(self, query: str, limit: int) -> list[dict]:
        query = query.casefold().strip()
        if not query:
            return []
        hits = [
            deepcopy(item) for item in self.screen["elements"]
            if query in item["label"].casefold() or query in (item["value"] or "").casefold()
        ][:limit]
        snapshot_id = self.remember(hits)
        return [dict(item, snapshot_id=snapshot_id) for item in hits]

    def element(self, snapshot_id: str, element_id: int) -> dict:
        if snapshot_id not in self.history:
            raise HelperError("stale_snapshot", f"{snapshot_id} expired; take a new snapshot")
        if element_id not in self.history[snapshot_id]:
            raise HelperError("element_not_found", f"Element {element_id} is absent from {snapshot_id}")
        return next(item for item in self.screen["elements"] if item["id"] == element_id)

    def subtree(self, element_id: int | None) -> tuple[list[dict], dict | None]:
        elements = self.screen["elements"]
        for index, item in enumerate(elements):
            if item["id"] != element_id:
                continue
            subtree = [item]
            for child in elements[index + 1:]:
                if child["depth"] <= item["depth"]:
                    break
                subtree.append(child)
            return subtree, item
        return [], None

    def read(self, snapshot_id: str, element_id: int) -> str:
        self.element(snapshot_id, element_id)
        items, _ = self.subtree(element_id)
        return "\n".join(item["value"] or item["label"] for item in items)

    def focus(self, target: dict) -> None:
        self.screen["focused_id"] = target["id"]
        for item in self.screen["elements"]:
            if "focused" in item["states"]:
                item["states"].remove("focused")
        target["states"].append("focused")
        self.refresh_text()

    def refresh_text(self) -> None:
        self.screen["text"] = render_screen(self.screen)

    def act(self, snapshot_id: str, element_id: int, action: str, value: str | int | float | bool | None) -> bool:
        item = self.element(snapshot_id, element_id)
        if "disabled" in item["states"]:
            raise HelperError("action_failed", f"Element {element_id} is disabled")
        if action == "focus":
            self.focus(item)
            return False
        if action == "set_value" and item["role"] == "checkbox" and isinstance(value, bool):
            states = item["states"]
            if value and "checked" not in states:
                states.append("checked")
            elif not value and "checked" in states:
                states.remove("checked")
            self.refresh_text()
            return False
        if action == "set_value" and item["role"] in EDITABLE | {"slider"}:
            if item["value"] == "(hidden)":
                raise HelperError("permission_denied", "Cannot set the value of a secure field")
            item["value"] = str(value)[:80]
            self.refresh_text()
            return False
        if action == "press" and item["role"] in PRESSABLE:
            label = item["label"].casefold().strip()
            if self.name == "mail_inbox" and label == "reply":
                return self.select("mail_reply_draft")
            if self.name == "mail_reply_draft" and label == "send":
                return self.select("mail_inbox")
            if item["role"] == "checkbox":
                states = item["states"]
                states.remove("checked") if "checked" in states else states.append("checked")
            elif item["role"] in {"radio", "row", "tab"}:
                if "selected" not in item["states"]:
                    item["states"].append("selected")
            self.focus(item)
            return False
        raise HelperError("action_unsupported", f"Cannot {action} the recorded {item['role']}")

    def type_text(self, text: str) -> int:
        focused = self.screen["focused_id"]
        item = next((item for item in self.screen["elements"] if item["id"] == focused), None)
        # The real Helper types into whatever has focus; Mail's compose body records as a
        # focused group ("message body"), not a text field.
        if item is None or item["role"] not in EDITABLE | {"group"} or "disabled" in item["states"]:
            raise HelperError("action_failed", "The recorded screen has no focused editable field")
        if item["value"] == "(hidden)":
            raise HelperError("permission_denied", "Cannot type into a secure field")
        item["value"] = ((item["value"] or "") + text)[:80]
        self.refresh_text()
        return len(text)

    def key(self, keys: str) -> bool:
        keys = keys.casefold().strip()
        if self.name == "mail_reply_draft" and keys in {"escape", "cmd+return"}:
            return self.select("mail_inbox")
        if keys in {"tab", "shift+tab"}:
            items = [item for item in self.screen["elements"] if item["role"] in EDITABLE | PRESSABLE
                     and "disabled" not in item["states"]]
            if items:
                shift = -1 if keys == "shift+tab" else 1
                fallback = len(items) if shift == -1 else -1
                index = next((index for index, item in enumerate(items)
                              if item["id"] == self.screen["focused_id"]), fallback)
                self.focus(items[(index + shift) % len(items)])
        return False

    def open_app(self, name: str) -> tuple[str, int, bool]:
        available = {"Mail": "mail_inbox", "Notes": "notes_list", "Safari": "safari_form"}
        folded = name.strip().casefold()
        exact = [app for app in available if app.casefold() == folded]
        matches = exact or [app for app in available if folded and app.casefold().startswith(folded)]
        if len(matches) != 1:
            raise HelperError("app_not_found", f"Unknown recorded app {name!r}", close_matches=matches)
        app = matches[0]
        changed = self.select(available[app])
        return app, list(available).index(app) + 300, changed
