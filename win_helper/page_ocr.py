"""Best-effort local OCR for the active window, using the Windows OCR language pack."""

import json
import os
import subprocess
from pathlib import Path


def read_window(hwnd: int) -> list[str]:
    script = Path(__file__).with_name("page_ocr.ps1")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-File", str(script), str(hwnd)],
            capture_output=True, encoding="utf-8", timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            lines = json.loads(result.stdout)
            if isinstance(lines, list):
                return [line for line in lines if isinstance(line, str) and line.strip()]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return []
