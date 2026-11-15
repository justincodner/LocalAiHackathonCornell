# Beacon Windows Helper

A Windows version of the Mac Helper (`helper/`), written in Python. It speaks the same contract v1
(`contracts/openapi.yaml`, `contracts/events.md`) on `127.0.0.1:8765`, so the Brain runs unchanged and
can't tell the two apart. Unlike the Mock Helper, it reads and drives the real desktop.

| Mac Helper | Windows Helper |
| --- | --- |
| macOS Accessibility (AX) | UI Automation, one cached subtree request per snapshot (`uia.py`) |
| Snapshot pipeline (`SnapshotBuilder.swift`) | Port of the same pipeline (`snapshot.py`) |
| CGEvent keys and clicks | `SendInput` (`win32.py`). Mac chords map: cmd→Ctrl, option→Alt, delete→Backspace, cmd+q→Alt+F4 (`keys.py`) |
| WhisperKit `tiny.en` | faster-whisper `tiny.en`, CPU int8, offline after setup (`stt.py`) |
| TTSKit / system voice | Windows SAPI voices (`speech.py`); `BEACON_WIN_VOICE=Zira` picks one |
| Hold Right Option | Hold **Right Alt** (`BEACON_PTT_KEY`: `right_alt`, `right_control`, `right_win`, `scroll_lock`, `pause`, `f13`). The key is swallowed, so no menu bar opens |
| HUD pill and highlight ring | Tk pill (never takes focus) and click-through ring (`hud.py`), plus a **Beacon** window where you can type requests |

## Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_helper.ps1   # once, online
powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1            # Bonsai + Helper + Brain
powershell -ExecutionPolicy Bypass -File scripts\stop_windows.ps1
```

`run_windows.ps1` starts anything that isn't already running and raises the Brain's timeouts for a CPU-only
laptop (`BEACON_LLM_TIMEOUT_S=60`, `BEACON_TASK_TIMEOUT_S=150`). It uses `brain/prompts/system_v3_windows.txt`,
which is v3 with "Mac" changed to "Windows PC". Logs go to `logs\bonsai.log`, `logs\helper.log` and `logs\brain.log`.

To run the Helper alone: `brain\.venv\Scripts\python.exe -m win_helper [--no-voice] [--no-hud] [--talk-key right_control]`.

## Behaviour worth knowing

- **Which window Beacon sees:** the frontmost window that isn't Beacon's own or the taskbar. Typing in the Beacon
  window sends the request, then hands focus back to the app you were using.
- **Only what's on screen:** snapshots ask UI Automation for onscreen elements. Firefox otherwise returns every
  background tab (15,000 nodes, 1.3 s); onscreen it's ~300 nodes in under 0.5 s.
- **Chromium and Electron** (Edge, Chrome, VS Code, Claude) build their accessibility tree on the first request.
  The Helper asks a second time if the first answer is nearly empty.
- **Web pages:** label text that repeats the field next to it is dropped. The page itself is never reported as
  `focused`. `/input/type` returns `action_failed` when only the page has focus, so the model focuses a field
  and tries again instead of typing into nothing.
- **Pressing:** Invoke, Toggle, Select, Expand, then the default action, then a real click at the element's
  centre (`performed_via: "click"`). Invoke waits for any modal dialog it opens, so after 2 s it counts as done.
- **Not implemented:** `/screen/capture` returns `permission_denied` (it's P2). Windows running as administrator
  can't be read or typed into from a normal process.

Tests for the pure parts (snapshot pipeline, chords): `brain\.venv\Scripts\python.exe -m pytest brain/tests/test_win_helper.py`.
