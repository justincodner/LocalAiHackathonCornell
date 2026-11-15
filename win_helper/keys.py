"""Key chords in the Mac Helper's syntax (cmd+return, escape, cmd+shift+d), translated to Windows keys.

The Brain and its risk guard speak Mac chords, so the Helper maps them: cmd -> Ctrl, option -> Alt, Mac
"delete" -> Backspace, and app-level shortcuts with a different Windows chord (cmd+q -> Alt+F4).
"""

from dataclasses import dataclass

from . import win32


class ChordError(ValueError):
    pass


MODIFIERS = {
    "cmd": win32.VK_CONTROL, "command": win32.VK_CONTROL, "meta": win32.VK_CONTROL, "super": win32.VK_CONTROL,
    "ctrl": win32.VK_CONTROL, "control": win32.VK_CONTROL,
    "shift": win32.VK_SHIFT,
    "alt": win32.VK_MENU, "opt": win32.VK_MENU, "option": win32.VK_MENU,
    "win": win32.VK_LWIN, "windows": win32.VK_LWIN,
}

NAMED = {
    "return": 0x0D, "enter": 0x0D, "ret": 0x0D, "keypadenter": 0x0D,
    "escape": 0x1B, "esc": 0x1B, "tab": 0x09, "space": 0x20, "spacebar": 0x20,
    "delete": 0x08, "backspace": 0x08, "forwarddelete": 0x2E, "fwddelete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "uparrow": 0x26, "downarrow": 0x28, "leftarrow": 0x25, "rightarrow": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pgup": 0x21, "pgdn": 0x22,
    "insert": 0x2D, "help": 0x70,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    "minus": 0xBD, "equal": 0xBB, "equals": 0xBB, "comma": 0xBC, "period": 0xBE, "dot": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE, "apostrophe": 0xDE,
    "grave": 0xC0, "backtick": 0xC0, "leftbracket": 0xDB, "rightbracket": 0xDD,
}

# Mac shortcuts whose Windows equivalent is not simply cmd -> ctrl.
REMAP = {"cmd+q": "alt+f4", "cmd+delete": "delete", "cmd+backspace": "delete", "cmd+,": "ctrl+,"}


@dataclass(frozen=True)
class Chord:
    modifiers: tuple[int, ...]
    key: int


def parse(raw: str) -> Chord:
    text = raw.strip()
    if not text:
        raise ChordError("keys must not be empty")
    parts = [p.strip().lower() for p in text.split("+")]
    if text.endswith("++") or text == "+":
        parts = [p for p in parts if p] + ["+"]
    if any(not p for p in parts):
        raise ChordError(f"bad key chord {raw!r}; use names like cmd+shift+d, cmd+return, escape")
    normal = "+".join("cmd" if p in {"command", "meta", "super"} else p for p in parts)
    if normal in REMAP:
        parts = REMAP[normal].split("+")
    mods: list[int] = []
    key: int | None = None
    for index, part in enumerate(parts):
        if part in MODIFIERS and index < len(parts) - 1:
            vk = MODIFIERS[part]
            if vk not in mods:
                mods.append(vk)
            continue
        if key is not None:
            raise ChordError(f"bad key chord {raw!r}: more than one non-modifier key")
        if part in NAMED:
            key = NAMED[part]
        elif len(part) == 1:
            found = win32.vk_for_char(part)
            if found is None:
                raise ChordError(f"no key for {part!r} on this keyboard layout")
            key, shifted = found
            if shifted and win32.VK_SHIFT not in mods:
                mods.append(win32.VK_SHIFT)
        elif part in MODIFIERS:  # a lone modifier, e.g. "shift"
            key = MODIFIERS[part]
        else:
            raise ChordError(f"unknown key {part!r} in {raw!r}")
    if key is None:
        raise ChordError(f"bad key chord {raw!r}: no key")
    return Chord(tuple(mods), key)


def press(raw: str) -> None:
    chord = parse(raw)
    win32.send_chord(list(chord.modifiers), chord.key)
