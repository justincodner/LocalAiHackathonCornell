"""Strip browser noise from screen labels before Beacon shows, speaks or reasons about them.

The Helper already drops Chrome's "- Memory usage - 383 MB" note and a tab's "(8623)" unread
counter (Labels in AXNode.swift); this is the same rule on the Brain side, so an older Helper
or a label that reached the snapshot another way is never read aloud with them.
"""

import re

_SPACE = r"[ \t\u00a0]"
MEMORY_NOTE = re.compile(
    rf"{_SPACE}*[-‐-―:|,]{_SPACE}*(?:high{_SPACE}+)?memory{_SPACE}+usage{_SPACE}*[-‐-―:]"
    rf"{_SPACE}*[0-9.,]+{_SPACE}*[KMGT]B\b",
    re.IGNORECASE,
)
UNREAD_COUNT = re.compile(r"^\(\d[\d,]*\+?\)\s+")
TAB_LINE_COUNT = re.compile(r'(\btab ")\(\d[\d,]*\+?\)\s+')


def clean(label: str, role: str = "") -> str:
    """A label without the memory note, and for tabs without the unread counter."""
    if not label:
        return label
    cleaned = MEMORY_NOTE.sub("", label).strip() or label
    if role == "tab":
        cleaned = UNREAD_COUNT.sub("", cleaned) or cleaned
    return cleaned


def clean_snapshot(snapshot: dict) -> dict:
    """The snapshot with every element label, the window title and the SCREEN text cleaned."""
    for item in snapshot.get("elements", []):
        if isinstance(item.get("label"), str):
            item["label"] = clean(item["label"], item.get("role", ""))
    if isinstance(snapshot.get("window"), str):
        snapshot["window"] = clean(snapshot["window"])
    if isinstance(snapshot.get("text"), str):
        snapshot["text"] = TAB_LINE_COUNT.sub(r"\1", MEMORY_NOTE.sub("", snapshot["text"]))
    return snapshot
