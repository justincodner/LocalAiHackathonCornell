"""Pure snapshot pipeline (spec 5.2-5.5), ported from the Mac Helper's SnapshotBuilder.swift.

Input: a tree of `Node`s read from UI Automation. Output: numbered elements and the SCREEN text block, in exactly
the shape the Mac Helper produces, so the Brain cannot tell the two apart.
""",

import json
import re
from dataclasses import dataclass, field

MAX_LENGTH = 80
MAX_DEPTH = 25
ROW_CAP = 20
STATE_ORDER = ["disabled", "focused", "selected", "checked", "expanded", "truncated", "inferred"]
INTERACTIVE = {"button", "link", "textfield", "textarea", "checkbox", "radio", "menuitem", "tab", "slider", "popup"}
ATOMIC = INTERACTIVE | {"heading", "row", "cell", "image"}
VALUED = {"textfield", "textarea", "slider", "checkbox", "radio", "popup"}
CONTAINERS = {"group", "list", "toolbar", "window", "menu"}


@dataclass
class Node:
    """One UI Automation element, already mapped to a Beacon role ("drop" and "transparent" are pseudo-roles)."""
    role: str
    name: str = ""
    help: str = ""
    automation_id: str = ""
    value: str | None = None
    password: bool = False
    enabled: bool = True
    focused: bool = False
    selected: bool = False
    toggle: int | None = None
    expanded: bool = False
    rect: tuple[float, float, float, float] | None = None
    children: list["Node"] = field(default_factory=list)
    ref: int | None = None

    @property
    def zero_size(self) -> bool:
        return self.rect is not None and (self.rect[2] <= 0 or self.rect[3] <= 0)


@dataclass
class Out:
    role: str
    label: str = ""
    label_source: str = "none"
    full_label: str = ""
    value: str | None = None
    states: set[str] = field(default_factory=set)
    rect: tuple[float, float, float, float] | None = None
    ref: int | None = None
    full_text: str | None = None
    children: list["Out"] = field(default_factory=list)
    row_count: int | None = None
    more_rows: int = 0
    collapsed: int = 0  # descendants left out by fit() to keep the snapshot under max_elements

    @property
    def interactive(self) -> bool:
        return self.role in INTERACTIVE

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)


@dataclass
class Element:
    id: int
    depth: int
    role: str
    label: str
    label_source: str
    value: str | None
    states: list[str]
    rect: tuple[float, float, float, float] | None
    ref: int | None
    full_text: str | None
    row_count: int | None

    def json(self) -> dict:
        r = self.rect or (0, 0, 0, 0)
        return {"id": self.id, "depth": self.depth, "role": self.role, "label": self.label, "value": self.value,
                "states": self.states, "bounds": [round(v) for v in r], "label_source": self.label_source}


@dataclass
class Built:
    elements: list[Element]
    lines: list[Element | tuple]  # (depth, n) "more rows", or (depth, n, "item") from fit()
    truncated: bool
    total: int

    @property
    def focused_id(self) -> int | None:
        return next((e.id for e in self.elements if "focused" in e.states), None)


# --- labels ----------------------------------------------------------------------------------------------------

PRIVATE_USE = re.compile("[-]")


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = PRIVATE_USE.sub("", str(text))  # icon-font glyphs (Segoe Fluent Icons) are not words
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def trim(text: str, limit: int = MAX_LENGTH) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - 3] + "...", True


TAB_NOISE = re.compile(r"\s+-\s+(?:high\s+)?memory usage\s+-\s+[0-9.,]+\s*[KMGT]B\b", re.I)


def descendant_texts(node: Node, include_fields: bool, limit: int = 12) -> list[str]:
    out: list[str] = []

    def visit(n: Node) -> None:
        if len(out) >= limit:
            return
        if n.role == "text":
            t = clean(n.name) or clean(n.value)
            if t:
                out.append(t)
            return
        if include_fields and n.role == "textfield" and not n.password and clean(n.value):
            out.append(clean(n.value))
            return
        for c in n.children:
            visit(c)

    for c in node.children:
        visit(c)
    return out


def first_image_label(node: Node) -> str | None:
    for c in node.children:
        if c.role == "image" and clean(c.name):
            return clean(c.name)
        found = first_image_label(c)
        if found:
            return found
    return None


def raw_label(node: Node, role: str) -> tuple[str, str] | None:
    if role == "text":
        t = clean(node.name) or clean(node.value)
        return (t, "title") if t else None
    if clean(node.name):
        return clean(node.name), "title"
    if clean(node.help):
        return clean(node.help), "help"
    if role not in ATOMIC:
        return None
    texts = descendant_texts(node, include_fields=role in {"row", "cell"})
    if texts:
        return (" - " if role == "row" else " ").join(texts), "child"
    img = first_image_label(node)
    return (img, "child") if img else None


def label_for(node: Node, role: str) -> tuple[str, str] | None:
    found = raw_label(node, role)
    if found and role == "tab":
        cleaned = TAB_NOISE.sub("", found[0])
        return (cleaned or found[0]), found[1]
    return found


def value_for(node: Node, role: str) -> str | None:
    if role not in VALUED:
        return None
    if node.password:
        return "(hidden)"
    if role in {"checkbox", "radio"}:
        if role == "radio":
            return "on" if node.selected else "off"
        if node.toggle is None:
            return None
        return "mixed" if node.toggle >= 2 else ("on" if node.toggle == 1 else "off")
    if role in {"textfield", "textarea"}:
        return clean(node.value) or ""
    return clean(node.value)


def is_checked(node: Node, role: str) -> bool:
    if role == "checkbox":
        return node.toggle == 1
    if role == "radio":
        return node.selected
    return False


def humanize(identifier: str | None) -> str | None:
    """`sendButton` -> "send button"; None for identifiers that carry no meaning (numbers, GUIDs, hex)."""
    raw = (identifier or "").strip()
    if not raw or re.search(r"0x[0-9a-fA-F]{4,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}|[0-9]{4,}", raw):
        return None
    if sum(ch.isdigit() for ch in raw) / len(raw) > 0.3:
        return None
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", raw)
    while words and words[-1].isdigit():
        words.pop()
    if not words or len(words) > 6 or not any(any(c.isalpha() for c in w) for w in words):
        return None
    return " ".join(w.lower() for w in words)


def near_label(sibling: str) -> str:
    return f"unlabeled, near '{trim(sibling, 60)[0]}'"


# --- transform -------------------------------------------------------------------------------------------------

def make_out(n: Node, role: str) -> Out:
    o = Out(role=role, rect=n.rect, ref=n.ref)
    found = label_for(n, role)
    if found:
        short, cut = trim(found[0])
        o.label, o.full_label, o.label_source = short, found[0], found[1]
        if cut:
            o.states.add("truncated")
    elif o.interactive:
        words = humanize(n.automation_id)
        if words:
            o.label, o.full_label, o.label_source = trim(words)[0], words, "inferred"
            o.states.add("inferred")
    v = value_for(n, role)
    if v is not None:
        short, cut = trim(v)
        o.value = short
        if cut:
            o.states.add("truncated")
    if role == "text" and o.label and len(o.full_label) > MAX_LENGTH:
        o.full_text = o.full_label
    if not n.enabled and role in INTERACTIVE | {"row"}:
        o.states.add("disabled")
    if n.focused and role in INTERACTIVE | {"row", "cell", "list"}:
        # Browsers give keyboard focus to the page itself; marked "focused", small models type into it.
        o.states.add("focused")
    if n.selected and role not in {"radio"}:
        o.states.add("selected")
    if is_checked(n, role):
        o.states.add("checked")
    if n.expanded:
        o.states.add("expanded")
    return o


def transform(n: Node) -> list[Out]:
    if n.role == "drop":
        return []
    if n.role == "transparent":
        return transform_children(n.children)
    role = n.role
    if n.zero_size:
        return transform_children(n.children) if role in CONTAINERS else []
    o = make_out(n, role)
    if role == "group":
        if not o.label:
            return transform_children(n.children)
        o.children = transform_children(n.children)
        if all(c.role == "text" and not c.children for c in o.children) and not o.states - {"truncated"}:
            # A web <label> or <legend>: a named group holding only its own words reads as plain text.
            o.role, o.children = "text", []
    elif role in {"image", "text"}:
        if not o.label:
            return []
    elif role == "list":
        o.children = transform_children(n.children)
        cap_rows(o)
    elif role in ATOMIC:
        keep_fields = role not in {"row", "cell"}
        inner = [x for c in n.children for x in transform(c)]
        o.children = finish_siblings(interactive_only(inner, keep_fields))
    else:
        o.children = transform_children(n.children)
    return [o]


def transform_children(children: list[Node]) -> list[Out]:
    return finish_siblings([x for c in children for x in transform(c)])


def interactive_only(outs: list[Out], keep_fields: bool) -> list[Out]:
    result: list[Out] = []
    for o in outs:
        is_field = o.role in {"textfield", "textarea"}
        if "focused" in o.states or (o.interactive and (keep_fields or not is_field)):
            result.append(o)
        else:
            result += interactive_only(o.children, keep_fields)
    return result


def finish_siblings(outs: list[Out]) -> list[Out]:
    return repair_from_siblings(merge_texts(drop_label_echoes(outs)))


def drop_label_echoes(outs: list[Out]) -> list[Out]:
    """Drops a text that only repeats the label of the control next to it ("First name" + textfield "First name"),
    the Windows form of the Mac Helper's duplicate radio/checkbox label rule."""
    def echo(i: int) -> bool:
        o = outs[i]
        if o.role != "text" or o.children or o.states - {"truncated"}:
            return False
        key = o.full_label.casefold().rstrip(" :*")
        return any(0 <= j < len(outs) and outs[j].interactive and outs[j].full_label.casefold().rstrip(" :*") == key
                   for j in (i - 1, i + 1))
    return [o for i, o in enumerate(outs) if not echo(i)]


def adjacent(a, b) -> bool:
    if not a or not b or a[2] <= 0 or a[3] <= 0 or b[2] <= 0 or b[3] <= 0:
        return True
    gap = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
    return gap <= 16


def union(a, b):
    if not a:
        return b
    if not b:
        return a
    x, y = min(a[0], b[0]), min(a[1], b[1])
    return (x, y, max(a[0] + a[2], b[0] + b[2]) - x, max(a[1] + a[3], b[1] + b[3]) - y)


def merge_texts(outs: list[Out]) -> list[Out]:
    result: list[Out] = []
    for o in outs:
        last = result[-1] if result else None
        if (o.role == "text" and not o.children and last is not None and last.role == "text"
                and not last.children and adjacent(last.rect, o.rect)):
            full = (last.full_text or last.full_label) + " " + (o.full_text or o.full_label)
            short, cut = trim(full)
            last.label, last.full_label, last.full_text = short, full, full
            if cut:
                last.states.add("truncated")
            last.states |= o.states - {"truncated"}
            last.rect = union(last.rect, o.rect)
        else:
            result.append(o)
    return result


def repair_from_siblings(outs: list[Out]) -> list[Out]:
    labels = [o.label or None for o in outs]
    for i, o in enumerate(outs):
        if not (o.interactive and not o.label):
            continue
        d = 1
        while i - d >= 0 or i + d < len(labels):
            j = i - d if i - d >= 0 and labels[i - d] else (i + d if i + d < len(labels) and labels[i + d] else None)
            if j is not None:
                text = near_label(labels[j])
                o.label, o.full_label, o.label_source = trim(text)[0], text, "inferred"
                o.states.add("inferred")
                break
            d += 1
    return outs


def cap_rows(o: Out) -> None:
    rows = [i for i, c in enumerate(o.children) if c.role == "row"]
    if rows:
        keep = set(rows[:ROW_CAP])
        o.children = [c for i, c in enumerate(o.children) if c.role != "row" or i in keep]
        o.row_count = len(rows)
        o.more_rows = len(rows) - len(keep)
    else:
        shown = len(o.children)
        o.children = o.children[:ROW_CAP]
        o.row_count = shown
        o.more_rows = shown - min(shown, ROW_CAP)


def flatten(outs, depth, max_elements, elements, lines, state) -> None:
    for o in outs:
        if depth > MAX_DEPTH or len(elements) >= max_elements:
            state["truncated"] = True
            return
        el = Element(len(elements) + 1, depth, o.role, o.label, o.label_source if o.label else "none", o.value,
                     [s for s in STATE_ORDER if s in o.states], o.rect, o.ref, o.full_text, o.row_count)
        elements.append(el)
        lines.append(el)
        flatten(o.children, depth + 1, max_elements, elements, lines, state)
        if o.more_rows > 0:
            lines.append((min(depth + 1, MAX_DEPTH), o.more_rows))
        if o.collapsed > 0:
            lines.append((min(depth + 1, MAX_DEPTH), o.collapsed, "item"))


# --- fitting a large window under max_elements -------------------------------------------------------------------
# Elements are kept in reading order, so a plain cut keeps the window's chrome (caption buttons, toolbars, sidebars,
# tab strips) and drops the content after it: in the Claude desktop app, a 60-element cut kept the sidebar and lost
# every chat message. fit() first removes controls that carry nothing, then collapses chrome containers, largest
# first, until the window fits. A collapsed container stays (its children are listed as "N more items"), anything
# holding keyboard focus is never collapsed, and /ax/find still searches the whole window.

CAPTION_BUTTONS = {"minimize", "maximize", "restore", "close"}
NOISE_BUTTON = re.compile(r"^(?:more options for|show message actions for)\b", re.I)
# Whole-label matches only: a web page's own group is named after its title ("New Tab", "Site navigation guide"),
# and collapsing that would hide the page itself.
CHROME_LABEL = re.compile(
    r"(?:(?:main|primary|left|right|browser) )?(?:side ?bar|navigation|nav|bookmarks(?: toolbar)?|tabs|tab ?strip"
    r"|menu ?bar|title ?bar|status ?bar|session tools|message actions)", re.I)


def has_focus(o: Out) -> bool:
    return "focused" in o.states or any(has_focus(c) for c in o.children)


def drop_noise(outs: list[Out], depth: int) -> list[Out]:
    kept = []
    for o in outs:
        if o.role == "button" and not o.children and (
                (depth <= 1 and o.full_label.casefold() in CAPTION_BUTTONS) or NOISE_BUTTON.match(o.full_label)):
            if "focused" not in o.states:
                continue
        o.children = drop_noise(o.children, depth + 1)
        kept.append(o)
    return kept


def is_chrome(o: Out) -> bool:
    return o.role in {"toolbar", "menu"} or (o.role in CONTAINERS and bool(CHROME_LABEL.fullmatch(o.full_label.strip())))


def chrome_containers(outs: list[Out]) -> list[Out]:
    found = []
    for o in outs:
        if o.children and is_chrome(o) and not has_focus(o):
            found.append(o)  # its own chrome children go with it
        else:
            found += chrome_containers(o.children)
    return found


def fit(top: list[Out], max_elements: int) -> list[Out]:
    if sum(o.count() for o in top) <= max_elements:
        return top
    top = drop_noise(top, 0)
    total = sum(o.count() for o in top)
    for o in sorted(chrome_containers(top), key=Out.count, reverse=True):
        if total <= max_elements:
            break
        hidden = o.count() - 1
        o.children, o.collapsed, o.more_rows = [], hidden, 0
        total -= hidden
    return top


def build(root: Node, include_root: bool, max_elements: int, walk_truncated: bool = False) -> Built:
    top = finish_siblings(transform(root)) if include_root else transform_children(root.children)
    total = sum(o.count() for o in top)
    top = fit(top, max(1, max_elements))
    elements: list[Element] = []
    lines: list = []
    state = {"truncated": walk_truncated}
    flatten(top, 0, max(1, max_elements), elements, lines, state)
    return Built(elements, lines, state["truncated"] or len(elements) < total, total)


# --- text block ------------------------------------------------------------------------------------------------

def quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def render(app: str, window: str, focused_id: int | None, lines: list) -> str:
    out = [f"APP {app} | WINDOW {quote(window)} | FOCUS {f'[{focused_id}]' if focused_id else 'none'}"]
    for line in lines:
        if isinstance(line, tuple):
            depth, count, *kind = line
            if kind:  # a container fit() collapsed
                out.append("  " * depth + f"... {count} more item{'' if count == 1 else 's'} (use find)")
            else:
                out.append("  " * depth + f"... {count} more row{'' if count == 1 else 's'} (scroll or use find)")
            continue
        s = "  " * line.depth + f"[{line.id}] {line.role}"
        if line.label:
            s += " " + quote(line.label)
        if line.row_count is not None:
            s += f" {line.row_count} row{'' if line.row_count == 1 else 's'}"
        if line.value is not None:
            s += " value=" + quote(line.value)
        if line.states:
            s += " (" + ", ".join(line.states) + ")"
        out.append(s)
    return "\n".join(out)
