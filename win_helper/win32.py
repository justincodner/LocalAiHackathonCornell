"""Thin ctypes layer over the Win32 calls the Windows Helper needs: windows, processes, keyboard and mouse input."""

import ctypes
import os
import time
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

OWN_PID = os.getpid()

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

GA_ROOT = 2
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def set_dpi_aware() -> None:
    """Per-monitor DPI awareness, so UIA bounds, the HUD and mouse input share physical pixels."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()


def foreground() -> int:
    hwnd = user32.GetForegroundWindow()
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd or 0)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


@lru_cache(maxsize=256)
def file_description(path: str) -> str:
    """The FileDescription of an executable ("Firefox", "Microsoft Edge"), or "" if it has none."""
    if not path:
        return ""
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return ""
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)):
        return ""
    langs = [(ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))[i * 2],
              ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))[i * 2 + 1]) for i in range(length.value // 4)]
    for lang, codepage in langs + [(0x0409, 0x04B0), (0x0409, 0x04E4)]:
        key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
        if version.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            text = ctypes.wstring_at(ptr, length.value).rstrip("\0").strip()
            if text:
                return text
    return ""


FRIENDLY = {
    "explorer": "File Explorer", "msedge": "Microsoft Edge", "chrome": "Google Chrome", "firefox": "Firefox",
    "notepad": "Notepad", "olk": "Outlook", "outlook": "Outlook", "winword": "Word", "excel": "Excel",
    "powerpnt": "PowerPoint", "windowsterminal": "Terminal", "code": "Visual Studio Code",
    "systemsettings": "Settings", "calculatorapp": "Calculator", "mspaint": "Paint",
}


def app_name(hwnd: int) -> str:
    """A short, speakable name for the app that owns a window."""
    path = process_path(window_pid(hwnd))
    stem = Path(path).stem
    if stem.casefold() == "applicationframehost":  # UWP apps: the frame process hosts them all
        title = window_title(hwnd)
        return title.rsplit(" - ", 1)[-1] if title else "App"
    if stem.casefold() in FRIENDLY:
        return FRIENDLY[stem.casefold()]
    return file_description(path) or stem or "Unknown"


def clean_window_title(title: str, app: str) -> str:
    """Drops a trailing " - Mozilla Firefox" style app suffix from a window title."""
    for sep in (" — ", " - ", " – "):
        head, found, tail = title.rpartition(sep)
        if found and head and (app.casefold() in tail.casefold() or tail.casefold() in app.casefold()):
            return head
    return title


def cloaked(hwnd: int) -> bool:
    """Windows 10+ keeps suspended UWP windows "visible" but cloaked (TextInputHost, background Settings)."""
    value = wintypes.DWORD()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(value), 4)  # DWMWA_CLOAKED
    except OSError:
        return False
    return bool(value.value)


def top_windows() -> list[int]:
    """Visible, un-owned, non-tool top-level windows in z-order (the ones a person would call apps)."""
    result: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW
                and user32.GetWindowTextLengthW(hwnd) > 0 and window_pid(hwnd) != OWN_PID and not cloaked(hwnd)
                and window_class(hwnd) not in {"Progman", "WorkerW", "Shell_TrayWnd"}):
            result.append(int(hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return result


def activate(hwnd: int) -> bool:
    """Brings a window to the front. Windows only lets the foreground process do this, so tap Alt first."""
    if not is_window(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if foreground() == hwnd:
        return True
    _key_event(VK_MENU, False)
    _key_event(VK_MENU, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    for _ in range(20):
        if foreground() == hwnd:
            return True
        time.sleep(0.02)
    return foreground() == hwnd


# --- SendInput -----------------------------------------------------------------------------------------------

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x2, 0x4, 0x800, 0x1000
WHEEL_DELTA = 120
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short

# Marks events the Helper injects, so its own keyboard hook ignores them.
BEACON_EXTRA = 0x42454143

VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12
VK_ESCAPE, VK_SPACE, VK_LWIN = 0x1B, 0x20, 0x5B
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5}


def _send(inputs: list[INPUT]) -> None:
    array = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(ctypes.get_last_error(), "SendInput was blocked (is an elevated window in front?)")


def _key_input(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, BEACON_EXTRA)))


def _key_event(vk: int, up: bool) -> None:
    _send([_key_input(vk, up)])


def send_chord(modifiers: list[int], key: int) -> None:
    downs = [_key_input(vk, False) for vk in modifiers] + [_key_input(key, False)]
    ups = [_key_input(key, True)] + [_key_input(vk, True) for vk in reversed(modifiers)]
    _send(downs + ups)


def type_unicode(text: str) -> int:
    """Types text as Unicode key events (layout-independent). Newlines press Return."""
    inputs: list[INPUT] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            inputs += [_key_input(VK_RETURN, False), _key_input(VK_RETURN, True)]
            continue
        for unit in _utf16_units(ch):
            inputs.append(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, BEACON_EXTRA))))
            inputs.append(INPUT(INPUT_KEYBOARD, _INPUTUNION(
                ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, BEACON_EXTRA))))
    for start in range(0, len(inputs), 200):  # chunks keep slow apps from dropping keys
        _send(inputs[start:start + 200])
        time.sleep(0.005)
    return len(text)


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def vk_for_char(ch: str) -> tuple[int, bool] | None:
    """Virtual key and whether shift is needed for a printable character on the current layout."""
    code = user32.VkKeyScanW(ch)
    if code == -1:
        return None
    return code & 0xFF, bool(code & 0x100)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def cursor_pos() -> tuple[int, int]:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def click(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    _send([INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, BEACON_EXTRA))),
           INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, BEACON_EXTRA)))])


def wheel(x: int, y: int, notches: int, horizontal: bool = False) -> None:
    """Scrolls at a point, then puts the cursor back. Positive notches scroll up (or right)."""
    old = cursor_pos()
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    data = ctypes.c_uint32(notches * WHEEL_DELTA).value
    _send([INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(0, 0, data, flag, 0, BEACON_EXTRA)))])
    time.sleep(0.02)
    user32.SetCursorPos(*old)


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def work_area() -> tuple[int, int, int, int]:
    r = RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def online() -> bool:
    flags = wintypes.DWORD()
    try:
        return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    except OSError:
        return False
