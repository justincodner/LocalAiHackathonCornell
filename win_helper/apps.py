"""Running and installed apps, and opening one by a spoken (fuzzy) name."""

import difflib
import json
import subprocess
import threading
import time

from . import win32
from .uia import HelperError

ALIASES = {
    "edge": "Microsoft Edge", "chrome": "Google Chrome", "browser": "Microsoft Edge", "explorer": "File Explorer",
    "files": "File Explorer", "file explorer": "File Explorer", "mail": "Outlook", "email": "Outlook",
    "word": "Word", "excel": "Excel", "powerpoint": "PowerPoint", "terminal": "Terminal",
    "calculator": "Calculator", "settings": "Settings", "notes": "Notepad", "safari": "Microsoft Edge",
}


class Apps:
    def __init__(self) -> None:
        self.installed: dict[str, str] = {}  # name -> AppUserModelID
        threading.Thread(target=self.load_installed, name="start-apps", daemon=True).start()

    def load_installed(self) -> None:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30, creationflags=0x08000000,  # CREATE_NO_WINDOW
            ).stdout
            items = json.loads(out or "[]")
            items = [items] if isinstance(items, dict) else items
            self.installed = {i["Name"]: i["AppID"] for i in items if i.get("Name") and i.get("AppID")}
        except (OSError, ValueError, subprocess.SubprocessError):
            self.installed = {}

    def running(self) -> dict[str, int]:
        """Friendly app name -> its frontmost window."""
        result: dict[str, int] = {}
        for hwnd in win32.top_windows():
            name = win32.app_name(hwnd)
            result.setdefault(name, hwnd)
        return result

    def listing(self) -> dict:
        return {"ok": True, "running": sorted(self.running()), "installed": sorted(self.installed)}

    @staticmethod
    def best(query: str, names: list[str]) -> str | None:
        q = query.casefold().strip()
        if not q:
            return None
        for name in names:
            if name.casefold() == q:
                return name
        starts = [n for n in names if n.casefold().startswith(q)]
        if starts:
            return min(starts, key=len)
        words = [n for n in names if q in n.casefold().split()]
        if words:
            return min(words, key=len)
        contains = [n for n in names if q in n.casefold()]
        if contains:
            return min(contains, key=len)
        close = difflib.get_close_matches(q, [n.casefold() for n in names], n=1, cutoff=0.8)
        return next((n for n in names if close and n.casefold() == close[0]), None)

    def open(self, query: str) -> dict:
        query = query.strip()
        wanted = ALIASES.get(query.casefold(), query)
        running = self.running()
        name = self.best(wanted, list(running)) or self.best(query, list(running))
        if name:
            hwnd = running[name]
            win32.activate(hwnd)
            return {"ok": True, "app": name, "pid": win32.window_pid(hwnd)}
        installed = list(self.installed)
        name = self.best(wanted, installed) or self.best(query, installed)
        if not name:
            close = difflib.get_close_matches(query, list(running) + installed, n=3, cutoff=0.4)
            raise HelperError("app_not_found", f"No app called {query!r}", close_matches=close)
        before = win32.foreground()
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{self.installed[name]}"], creationflags=0x08000000)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            time.sleep(0.2)
            fg = win32.foreground()
            if fg and fg != before and win32.window_pid(fg) != win32.OWN_PID:
                time.sleep(0.4)  # let the first window finish drawing before the Brain snapshots it
                return {"ok": True, "app": win32.app_name(fg), "pid": win32.window_pid(fg)}
        return {"ok": True, "app": name, "pid": 0}
