"""Push-to-talk: a low-level keyboard hook that watches one key (Right Alt by default) and swallows it, so a bare
Alt tap never opens an app's menu bar."""

import ctypes
import queue
import threading
from ctypes import wintypes

from . import win32

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10

KEYS = {
    "right_alt": 0xA5, "right_option": 0xA5, "right_control": 0xA3, "right_ctrl": 0xA3,
    "right_command": 0xA3, "right_win": 0x5C, "scroll_lock": 0x91, "pause": 0x13, "f13": 0x7C,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
win32.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
win32.user32.SetWindowsHookExW.restype = wintypes.HHOOK
win32.user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
win32.user32.CallNextHookEx.restype = ctypes.c_ssize_t
win32.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]


class TalkKey:
    def __init__(self, name: str, on_down, on_up) -> None:
        self.name = name if name in KEYS else "right_alt"
        self.vk = KEYS[self.name]
        self.on_down, self.on_up = on_down, on_up
        self.down = False
        self.proc = HOOKPROC(self.hook)  # keep a reference: the hook must outlive this call
        self.presses: queue.Queue = queue.Queue()  # one worker keeps down/up in order and the hook fast
        threading.Thread(target=self.run, name="talk-key", daemon=True).start()
        threading.Thread(target=self.work, name="talk-key-work", daemon=True).start()

    def work(self) -> None:
        while True:
            handler = self.presses.get()
            try:
                handler()
            except Exception:  # noqa: BLE001 - a failed press must not stop the key
                pass

    def hook(self, code, wparam, lparam):
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode == self.vk and info.dwExtraInfo != win32.BEACON_EXTRA:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self.down:  # ignore auto-repeat
                        self.down = True
                        self.presses.put(self.on_down)
                    return 1
                if wparam in (WM_KEYUP, WM_SYSKEYUP):
                    if self.down:
                        self.down = False
                        self.presses.put(self.on_up)
                    return 1
        return win32.user32.CallNextHookEx(None, code, wparam, lparam)

    def run(self) -> None:
        hook = win32.user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.proc, None, 0)
        if not hook:
            return
        msg = wintypes.MSG()
        while win32.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            win32.user32.TranslateMessage(ctypes.byref(msg))
            win32.user32.DispatchMessageW(ctypes.byref(msg))
