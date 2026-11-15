"""UI Automation: reads the frontmost window into snapshot Nodes and performs actions on snapshot elements.

Every call here runs on COM's multithreaded apartment (see `com_thread`). The whole subtree is fetched with one
cache request, so a 3,000-node browser window reads in about 0.1 s.
"""

import sys

sys.coinit_flags = 0  # COINIT_MULTITHREADED, before comtypes is imported anywhere

import ctypes  # noqa: E402
import itertools  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import OrderedDict  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402

from . import snapshot as snap  # noqa: E402
from . import win32  # noqa: E402

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as U  # noqa: E402


class HelperError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.error = {"code": code, "message": message, **details}


def com_thread() -> None:
    """Thread initializer: join COM's multithreaded apartment."""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass


_local = threading.local()


def automation():
    if getattr(_local, "auto", None) is None:
        com_thread()
        _local.auto = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
    return _local.auto


P = {
    "ctype": U.UIA_ControlTypePropertyId, "name": U.UIA_NamePropertyId, "aid": U.UIA_AutomationIdPropertyId,
    "rect": U.UIA_BoundingRectanglePropertyId, "enabled": U.UIA_IsEnabledPropertyId,
    "focus": U.UIA_HasKeyboardFocusPropertyId, "offscreen": U.UIA_IsOffscreenPropertyId,
    "value": U.UIA_ValueValuePropertyId, "readonly": U.UIA_ValueIsReadOnlyPropertyId,
    "password": U.UIA_IsPasswordPropertyId, "toggle": U.UIA_ToggleToggleStatePropertyId,
    "selected": U.UIA_SelectionItemIsSelectedPropertyId, "expand": U.UIA_ExpandCollapseExpandCollapseStatePropertyId,
    "help": U.UIA_HelpTextPropertyId, "range": U.UIA_RangeValueValuePropertyId,
    "has_value": U.UIA_IsValuePatternAvailablePropertyId, "has_text": U.UIA_IsTextPatternAvailablePropertyId,
    "has_range": U.UIA_IsRangeValuePatternAvailablePropertyId,
}
HEADING = getattr(U, "UIA_HeadingLevelPropertyId", None)
if HEADING:
    P["heading"] = HEADING
P_ARIA = getattr(U, "UIA_AriaRolePropertyId", None)
if P_ARIA:
    P["aria"] = P_ARIA

ROLE_BY_TYPE = {
    50000: "button", 50001: "group", 50002: "checkbox", 50003: "popup", 50004: "textfield", 50005: "link",
    50006: "image", 50007: "row", 50008: "list", 50009: "menu", 50010: "menu", 50011: "menuitem",
    50012: "drop", 50013: "radio", 50014: "drop", 50015: "slider", 50016: "slider", 50017: "group",
    50018: "group", 50019: "tab", 50020: "text", 50021: "toolbar", 50022: "drop", 50023: "list",
    50024: "row", 50025: "group", 50026: "group", 50027: "drop", 50028: "list", 50029: "row",
    50030: "document", 50031: "button", 50032: "window", 50033: "group", 50034: "group", 50035: "button",
    50036: "list", 50037: "drop", 50038: "drop", 50039: "group", 50040: "toolbar",
}
HEADING_NONE = 80050
MAX_RAW_NODES = 6000
SNAPSHOTS_KEPT = 5


@dataclass
class Target:
    hwnd: int
    app: str
    window: str


class Registry:
    """Snapshot id -> element id -> live UIA element, for the last few snapshots."""

    def __init__(self) -> None:
        self.ids = itertools.count(1)
        self.items: OrderedDict[str, dict[int, tuple[object, snap.Element, snap.Node | None]]] = OrderedDict()
        self.hwnd: dict[str, int] = {}
        self.lock = threading.Lock()

    def add(self, hwnd: int, entries: dict[int, tuple[object, snap.Element, snap.Node | None]]) -> str:
        with self.lock:
            sid = f"s_{next(self.ids):04d}"
            self.items[sid] = entries
            self.hwnd[sid] = hwnd
            while len(self.items) > SNAPSHOTS_KEPT:
                old, _ = self.items.popitem(last=False)
                self.hwnd.pop(old, None)
            return sid

    def get(self, sid: str, eid: int):
        with self.lock:
            if sid not in self.items:
                raise HelperError("stale_snapshot", f"{sid} expired; take a new snapshot")
            if eid not in self.items[sid]:
                raise HelperError("element_not_found", f"Element {eid} is absent from {sid}")
            return self.items[sid][eid], self.hwnd[sid]


class Reader:
    def __init__(self, target_window) -> None:
        """`target_window()` returns the hwnd Beacon should look at (the frontmost window that isn't Beacon's)."""
        self.target_window = target_window
        self.registry = Registry()

    # --- reading -----------------------------------------------------------------------------------------------

    def cache_request(self, onscreen: bool = True):
        """Onscreen-only control view: Firefox keeps every background tab in the tree (15,000 nodes, 1.3 s);
        filtering in the provider leaves the ~300 a person can see, in under 0.5 s."""
        auto = automation()
        cr = auto.CreateCacheRequest()
        for pid in P.values():
            cr.AddProperty(pid)
        cr.TreeScope = U.TreeScope_Subtree
        cr.TreeFilter = auto.ControlViewCondition
        if onscreen:
            cr.TreeFilter = auto.CreateAndCondition(
                auto.ControlViewCondition, auto.CreatePropertyCondition(U.UIA_IsOffscreenPropertyId, False))
        return cr

    def target(self) -> Target:
        hwnd = self.target_window()
        if not hwnd:
            raise HelperError("element_not_found", "No window is in front")
        app = win32.app_name(hwnd)
        return Target(hwnd, app, win32.clean_window_title(win32.window_title(hwnd), app))

    def root(self, hwnd: int, onscreen: bool = True):
        try:
            return automation().ElementFromHandleBuildCache(hwnd, self.cache_request(onscreen))
        except comtypes.COMError as exc:
            raise HelperError("permission_denied", f"Cannot read this window ({exc.hresult:#x}); "
                              "it may be running as administrator") from exc

    @staticmethod
    def prop(el, key: str):
        try:
            return el.GetCachedPropertyValue(P[key])
        except (comtypes.COMError, KeyError):
            return None

    def to_node(self, el, raw: list, budget: dict) -> snap.Node:
        g = lambda k: self.prop(el, k)  # noqa: E731
        ctype = g("ctype") or 0
        role = ROLE_BY_TYPE.get(ctype, "group")
        rect = g("rect")
        rect = tuple(float(v) for v in rect) if rect is not None and len(rect) == 4 else None
        offscreen = bool(g("offscreen"))
        children_arr = el.GetCachedChildren()
        kids = []
        if children_arr:
            for i in range(children_arr.Length):
                if budget["n"] >= MAX_RAW_NODES:
                    budget["truncated"] = True
                    break
                budget["n"] += 1
                kids.append(children_arr.GetElement(i))
        heading = g("heading") if HEADING else None
        aria = (g("aria") or "") if P_ARIA else ""
        if heading and heading != HEADING_NONE or aria == "heading":
            role = "heading"
        if role == "document":
            # Notepad/RichEdit documents are leaf editors; a browser's page root has children (and its URL as value).
            url = str(g("value") or "")
            is_page = "://" in url[:16] or url.startswith(("about:", "chrome:", "edge:"))
            role = "textarea" if not kids and not is_page and (g("has_text") or g("has_value")) else "group"
            if role == "group":
                g = (lambda base: lambda k: None if k == "value" else base(k))(g)  # noqa: E731
        if role == "textfield" and (rect and rect[3] > 60) and aria != "textbox":
            role = "textarea" if not g("readonly") else role
        if offscreen:
            role = "transparent" if role in snap.CONTAINERS | {"document"} else "drop"
        value = g("value")
        if value is None and role == "slider" and g("has_range"):
            r = g("range")
            value = None if r is None else (str(int(r)) if float(r).is_integer() else f"{r:g}")
        expand = g("expand")
        node = snap.Node(
            role=role, name=g("name") or "", help=g("help") or "", automation_id=g("aid") or "",
            value=None if value is None else str(value), password=bool(g("password")),
            enabled=g("enabled") is not False, focused=bool(g("focus")), selected=bool(g("selected")),
            toggle=g("toggle"), expanded=expand == 1, rect=rect, ref=len(raw),
        )
        raw.append(el)
        node.children = [self.to_node(k, raw, budget) for k in kids]
        if role == "textarea" and not node.value and not node.password and g("has_text"):
            node.value = self.text_of(el, 200)
        return node

    @staticmethod
    def text_of(el, limit: int) -> str | None:
        try:
            pattern = el.GetCurrentPattern(U.UIA_TextPatternId).QueryInterface(U.IUIAutomationTextPattern)
            text = pattern.DocumentRange.GetText(limit)
            return text.replace("\r\n", "\n").replace("\r", "\n") if text else None
        except (comtypes.COMError, ValueError, AttributeError):
            return None

    def read_tree(self, hwnd: int, onscreen: bool = True):
        raw: list = []
        budget = {"n": 0, "truncated": False}
        tree = self.to_node(self.root(hwnd, onscreen), raw, budget)
        if len(raw) < 12 and win32.window_class(hwnd) == "Chrome_WidgetWin_1" and not getattr(self, "_woke", {}).get(hwnd):
            # Chromium and Electron build their accessibility tree on the first request; ask again once.
            self._woke = {**getattr(self, "_woke", {}), hwnd: True}
            time.sleep(0.4)
            return self.read_tree(hwnd, onscreen)
        if len(raw) < 3 and onscreen:
            return self.read_tree(hwnd, onscreen=False)
        return tree, raw, budget["truncated"]

    def snapshot(self, scope: str, max_elements: int) -> dict:
        started = time.perf_counter()
        target = self.target()
        include_root = False
        if scope == "menu":
            menu = self.open_menu()
            if menu is not None:
                tree, raw, walk_cut = menu
            else:
                tree, raw, walk_cut = self.read_tree(target.hwnd)
                tree = snap.Node(role="group", children=list(find_nodes(tree, {"menu", "menuitem"})))
        else:
            tree, raw, walk_cut = self.read_tree(target.hwnd)
            if scope == "focused":
                focused = next(find_focused(tree), None)
                if focused is not None:
                    tree, include_root = focused, True
        built = snap.build(tree, include_root, max_elements, walk_cut)
        nodes_by_ref = {}
        index_nodes(tree, nodes_by_ref)
        entries = {e.id: (raw[e.ref] if e.ref is not None else None, e, nodes_by_ref.get(e.ref))
                   for e in built.elements}
        sid = self.registry.add(target.hwnd, entries)
        focused_id = built.focused_id
        return {
            "ok": True, "snapshot_id": sid, "app": target.app, "window": target.window, "focused_id": focused_id,
            "truncated": built.truncated, "total_elements": built.total,
            "build_ms": int((time.perf_counter() - started) * 1000),
            "elements": [e.json() for e in built.elements],
            "text": snap.render(target.app, target.window, focused_id, built.lines),
        }

    def open_menu(self):
        """Pop-up menus are their own top-level windows on Windows (class #32768, or a Menu element)."""
        auto = automation()
        cond = auto.CreatePropertyCondition(U.UIA_ControlTypePropertyId, 50009)
        try:
            menu = auto.GetRootElement().FindFirstBuildCache(U.TreeScope_Children, cond, self.cache_request())
        except comtypes.COMError:
            return None
        if not menu:
            return None
        raw: list = []
        budget = {"n": 0, "truncated": False}
        return snap.Node(role="group", children=[self.to_node(menu, raw, budget)]), raw, budget["truncated"]

    def find(self, query: str, limit: int) -> list[dict]:
        target = self.target()
        tree, raw, _ = self.read_tree(target.hwnd)
        q = query.casefold().strip()
        if not q:
            return []
        hits: list[snap.Node] = []

        def visit(n: snap.Node) -> None:
            if len(hits) >= limit:
                return
            if n.role not in {"drop", "transparent"} and (
                    q in n.name.casefold() or q in (n.value or "").casefold() or q in n.help.casefold()):
                hits.append(n)
            for c in n.children:
                visit(c)

        visit(tree)
        entries, results = {}, []
        for i, n in enumerate(hits, start=1):
            role = n.role if n.role in snap.INTERACTIVE | snap.ATOMIC | snap.CONTAINERS | {"text"} else "group"
            out = snap.make_out(n, role)
            el = snap.Element(i, 0, role, out.label, out.label_source if out.label else "none", out.value,
                              [s for s in snap.STATE_ORDER if s in out.states], n.rect, n.ref, None, None)
            entries[i] = (raw[n.ref], el, n)
            results.append(el.json())
        sid = self.registry.add(target.hwnd, entries)
        return [dict(r, snapshot_id=sid) for r in results]

    def read(self, sid: str, eid: int) -> str:
        (live, el, node), _ = self.registry.get(sid, eid)
        if el.role in {"textarea", "textfield"} and live is not None:
            if node is not None and node.password:
                raise HelperError("permission_denied", "Beacon does not read secure fields")
            text = self.text_of(live, 20000)
            if text:
                return text
        if node is None:
            return el.full_text or el.label
        parts: list[str] = []

        def visit(n: snap.Node) -> None:
            if n.role == "drop":
                return
            t = snap.clean(n.value) if n.role in {"textfield", "textarea"} and not n.password else None
            t = t or snap.clean(n.name)
            if t and (not parts or parts[-1] != t):
                parts.append(t)
            for c in n.children:
                visit(c)

        visit(node)
        return "\n".join(parts) or el.label

    # --- acting ------------------------------------------------------------------------------------------------

    def live(self, sid: str, eid: int):
        (live, el, node), hwnd = self.registry.get(sid, eid)
        if live is None:
            raise HelperError("element_not_found", f"Element {eid} has no live reference")
        try:
            live.CurrentProcessId  # noqa: B018 - throws if the element is gone
        except comtypes.COMError as exc:
            raise HelperError("stale_snapshot", "The screen changed; take a new snapshot") from exc
        return live, el, node, hwnd

    @staticmethod
    def pattern(live, pattern_id, interface):
        try:
            unk = live.GetCurrentPattern(pattern_id)
        except comtypes.COMError:
            return None
        if not unk:
            return None
        try:
            return unk.QueryInterface(interface)
        except (comtypes.COMError, ValueError):
            return None

    def ensure_front(self, hwnd: int) -> None:
        if win32.foreground() != hwnd:
            win32.activate(hwnd)

    def act(self, sid: str, eid: int, action: str, value) -> str:
        live, el, node, hwnd = self.live(sid, eid)
        if "disabled" in el.states:
            raise HelperError("action_failed", f"Element {eid} is disabled")
        self.ensure_front(hwnd)
        try:
            if action == "focus":
                live.SetFocus()
                return "ax"
            if action == "press":
                return self.press(live, el)
            if action == "set_value":
                return self.set_value(live, el, node, value)
            if action == "show_menu":
                p = self.pattern(live, U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern)
                if p is not None:
                    p.Expand()
                    return "ax"
                live.SetFocus()
                from . import keys
                keys.press("shift+f10")
                return "ax"
            if action in {"increment", "decrement"}:
                p = self.pattern(live, U.UIA_RangeValuePatternId, U.IUIAutomationRangeValuePattern)
                if p is not None:
                    step = p.CurrentSmallChange or 1
                    new = p.CurrentValue + (step if action == "increment" else -step)
                    p.SetValue(max(p.CurrentMinimum, min(p.CurrentMaximum, new)))
                    return "ax"
                live.SetFocus()
                from . import keys
                keys.press("up" if action == "increment" else "down")
                return "ax"
        except comtypes.COMError as exc:
            raise HelperError("action_failed", f"{action} failed on element {eid} ({exc.hresult:#x})") from exc
        raise HelperError("action_unsupported", f"Unknown action {action}")

    def press(self, live, el: snap.Element) -> str:
        attempts = [
            (U.UIA_InvokePatternId, U.IUIAutomationInvokePattern, lambda p: p.Invoke()),
            (U.UIA_TogglePatternId, U.IUIAutomationTogglePattern, lambda p: p.Toggle()),
            (U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern, lambda p: p.Select()),
            (U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern,
             lambda p: p.Collapse() if p.CurrentExpandCollapseState == 1 else p.Expand()),
            (U.UIA_LegacyIAccessiblePatternId, U.IUIAutomationLegacyIAccessiblePattern,
             lambda p: p.DoDefaultAction() if p.CurrentDefaultAction else (_ for _ in ()).throw(ValueError())),
        ]
        for pattern_id, interface, run in attempts:
            p = self.pattern(live, pattern_id, interface)
            if p is None:
                continue
            if self.run_bounded(lambda: run(p)):
                return "ax"
        rect = el.rect
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            raise HelperError("action_unsupported", f"Element {el.id} cannot be pressed and has no position")
        win32.click(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
        return "click"

    @staticmethod
    def run_bounded(fn, timeout: float = 2.0) -> bool:
        """Runs a pattern call on its own thread. Invoke blocks until a modal dialog it opens is closed, so a
        call still running after `timeout` counts as done."""
        result: dict = {}

        def work():
            com_thread()
            try:
                fn()
                result["ok"] = True
            except (comtypes.COMError, ValueError, OSError) as exc:
                result["error"] = exc

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout)
        return t.is_alive() or result.get("ok", False)

    def set_value(self, live, el: snap.Element, node, value) -> str:
        if node is not None and node.password:
            raise HelperError("permission_denied", "Cannot set the value of a secure field")
        if el.role in {"checkbox", "radio"} and isinstance(value, bool):
            if el.role == "radio":
                if value:
                    return self.press(live, el)
                return "ax"
            p = self.pattern(live, U.UIA_TogglePatternId, U.IUIAutomationTogglePattern)
            if p is None:
                raise HelperError("action_unsupported", "This checkbox cannot be toggled")
            for _ in range(3):
                if (p.CurrentToggleState == 1) == value:
                    return "ax"
                p.Toggle()
            return "ax"
        if el.role == "slider":
            p = self.pattern(live, U.UIA_RangeValuePatternId, U.IUIAutomationRangeValuePattern)
            if p is not None:
                p.SetValue(float(value))
                return "ax"
        p = self.pattern(live, U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
        if p is not None and not p.CurrentIsReadOnly:
            p.SetValue(str(value))
            return "ax"
        # Rich editors (Word, web contenteditable): focus, select all, type.
        live.SetFocus()
        from . import keys
        keys.press("ctrl+a")
        if str(value):
            win32.type_unicode(str(value))
        else:
            keys.press("delete")
        return "ax"

    def check_typing_target(self) -> None:
        """Refuses to type when focus is on a web page or bare window rather than a field: on the Mac the model
        then learns from the error and focuses the field, instead of typing into nothing and saying it's done.
        Focus moves asynchronously after /ax/act focus, so give it up to 400 ms to land."""
        for attempt in range(8):
            try:
                self._check_typing_target()
                return
            except HelperError:
                if attempt == 7:
                    raise
                time.sleep(0.05)

    def _check_typing_target(self) -> None:
        try:
            focused = automation().GetFocusedElement()
            ctype = focused.CurrentControlType
        except comtypes.COMError:
            return
        if ctype in {50032, 50033}:  # Window, Pane
            raise HelperError("action_failed", "No text field has focus; focus or click a field first")
        if ctype == 50030:
            try:
                value = self.pattern(focused, U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
                url = (value.CurrentValue or "") if value is not None else ""
            except comtypes.COMError:
                url = ""
            if "://" in url[:16] or url.startswith("about:"):
                raise HelperError("action_failed", "The web page has focus, not a text field; focus a field first")

    def bounds(self, sid: str, eid: int):
        (_, el, _), _ = self.registry.get(sid, eid)
        return el.rect

    def scroll(self, direction: str, amount, sid: str | None, eid: int | None) -> None:
        if sid is not None and eid is not None:
            live, el, _, hwnd = self.live(sid, eid)
            p = self.pattern(live, U.UIA_ScrollPatternId, U.IUIAutomationScrollPattern)
            if p is not None and amount == "page":
                big = {"up": (2, 1), "down": (2, 4), "left": (1, 2), "right": (4, 2)}[direction]  # (h, v)
                try:
                    p.Scroll(*big)
                    return
                except comtypes.COMError:
                    pass
            rect = el.rect or win32.window_rect(hwnd)
        else:
            hwnd = self.target_window()
            self.ensure_front(hwnd)
            rect = win32.window_rect(hwnd)
        notches = 5 if amount == "page" else max(1, round(int(amount) / 50))
        sign = 1 if direction in {"up", "right"} else -1
        win32.wheel(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2, sign * notches, direction in {"left", "right"})


def find_nodes(node: snap.Node, roles: set[str]):
    if node.role in roles:
        yield node
        return
    for c in node.children:
        yield from find_nodes(c, roles)


def find_focused(node: snap.Node):
    if node.focused:
        yield node
    for c in node.children:
        yield from find_focused(c)


def index_nodes(node: snap.Node, out: dict) -> None:
    if node.ref is not None:
        out[node.ref] = node
    for c in node.children:
        index_nodes(c, out)


_ = ctypes  # keep import for type users
