"""On-screen HUD (Tk, its own thread): a status pill that never takes focus, a highlight ring around the element
Beacon is acting on, and a small "Beacon" window to type requests when speaking isn't an option."""

import ctypes
import queue
import threading
import tkinter as tk

from . import win32

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = (
    0x80000, 0x20, 0x80, 0x08000000, 0x8)

COLORS = {
    "idle": "#5f6b7a", "listening": "#d93025", "thinking": "#f9ab00", "acting": "#1a73e8",
    "speaking": "#188038", "confirm": "#a142f4", "error": "#d93025",
}
WORDS = {
    "idle": "Beacon", "listening": "Listening…", "thinking": "Thinking…", "acting": "Working…",
    "speaking": "Speaking", "confirm": "Waiting for yes or no", "error": "Problem",
}
RING_KEY = "#ff00fe"  # drawn transparent


def _style(window: tk.Misc, add: int) -> None:
    hwnd = int(window.wm_frame(), 16)
    style = win32.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    win32.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | add)


class HUD:
    def __init__(self, talk_key_label: str, on_typed) -> None:
        self.talk_key_label = talk_key_label
        self.on_typed = on_typed
        self.calls: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        threading.Thread(target=self.run, name="hud", daemon=True).start()
        self.ready.wait(5)

    # --- thread-safe API -----------------------------------------------------------------------------------

    def state(self, state: str, transcript: str | None = None, caption: str | None = None) -> None:
        self.calls.put(lambda: self._state(state, transcript, caption))

    def highlight(self, rect) -> None:
        self.calls.put(lambda: self._ring(rect))

    def log(self, who: str, text: str) -> None:
        self.calls.put(lambda: self._log(who, text))

    # --- Tk thread -----------------------------------------------------------------------------------------

    def run(self) -> None:
        self.root = tk.Tk()
        self.root.title("Beacon")
        self.root.configure(bg="#1f2329")
        self.root.attributes("-topmost", False)
        x, y, w, h = win32.work_area()
        self.root.geometry(f"420x300+{x + w - 440}+{y + h - 320}")
        self.root.protocol("WM_DELETE_WINDOW", self.root.iconify)  # closing hides; Ctrl+C in the console quits
        tk.Label(self.root, text=f"Hold {self.talk_key_label} and speak, or type below and press Enter.",
                 bg="#1f2329", fg="#c8ccd2", anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=8, pady=(8, 2))
        self.history = tk.Text(self.root, height=10, bg="#15181d", fg="#e8eaed", relief="flat", wrap="word",
                               font=("Segoe UI", 10), state="disabled")
        self.history.tag_configure("you", foreground="#8ab4f8")
        self.history.tag_configure("beacon", foreground="#e8eaed")
        self.history.tag_configure("note", foreground="#9aa0a6")
        self.history.pack(fill="both", expand=True, padx=8, pady=4)
        self.entry = tk.Entry(self.root, bg="#2b3038", fg="#ffffff", insertbackground="#ffffff", relief="flat",
                              font=("Segoe UI", 11))
        self.entry.pack(fill="x", padx=8, pady=(0, 8), ipady=4)
        self.entry.bind("<Return>", self._submit)

        self.pill = tk.Toplevel(self.root)
        self.pill.overrideredirect(True)
        self.pill.attributes("-topmost", True)
        self.pill.attributes("-alpha", 0.93)
        self.pill.configure(bg="#202124")
        self.dot = tk.Canvas(self.pill, width=14, height=14, bg="#202124", highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=COLORS["idle"], outline="")
        self.dot.pack(side="left", padx=(12, 6), pady=8)
        self.caption = tk.Label(self.pill, text="", bg="#202124", fg="#ffffff", font=("Segoe UI", 11),
                                wraplength=640, justify="left")
        self.caption.pack(side="left", padx=(0, 14), pady=8)

        self.ring = tk.Toplevel(self.root)
        self.ring.overrideredirect(True)
        self.ring.attributes("-topmost", True)
        self.ring.configure(bg=RING_KEY)
        self.ring.attributes("-transparentcolor", RING_KEY)
        self.ring_canvas = tk.Canvas(self.ring, bg=RING_KEY, highlightthickness=0)
        self.ring_canvas.pack(fill="both", expand=True)
        self.ring.withdraw()
        self.ring_timer = None

        self.root.update_idletasks()
        _style(self.pill, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        _style(self.ring, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        self._state("idle", None, None)
        self._log("note", f"Beacon is ready. Hold {self.talk_key_label} to talk.")
        self.root.after(30, self._pump)
        self.ready.set()
        self.root.mainloop()

    def _pump(self) -> None:
        try:
            while True:
                self.calls.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(30, self._pump)

    def _place_pill(self) -> None:
        self.pill.update_idletasks()
        x, y, w, h = win32.work_area()
        pw, ph = self.pill.winfo_reqwidth(), self.pill.winfo_reqheight()
        self.pill.geometry(f"+{x + (w - pw) // 2}+{y + h - ph - 24}")

    def _state(self, state: str, transcript: str | None, caption: str | None) -> None:
        self.dot.itemconfigure(self.dot_id, fill=COLORS.get(state, COLORS["idle"]))
        text = caption or transcript or WORDS.get(state, state)
        if state == "idle" and not caption:
            text = f"Beacon · hold {self.talk_key_label} to talk"
        self.caption.configure(text=text if len(text) < 240 else text[:237] + "…")
        self._place_pill()
        if state == "idle":
            self._hide_ring()

    def _ring(self, rect) -> None:
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            return
        pad = 4
        x, y, w, h = int(rect[0]) - pad, int(rect[1]) - pad, int(rect[2]) + 2 * pad, int(rect[3]) + 2 * pad
        self.ring.geometry(f"{w}x{h}+{x}+{y}")
        self.ring_canvas.delete("all")
        self.ring_canvas.create_rectangle(2, 2, w - 3, h - 3, outline="#fbbc04", width=4)
        self.ring.deiconify()
        self.ring.attributes("-topmost", True)
        if self.ring_timer:
            self.root.after_cancel(self.ring_timer)
        self.ring_timer = self.root.after(4000, self._hide_ring)

    def _hide_ring(self) -> None:
        self.ring.withdraw()
        self.ring_timer = None

    def _log(self, who: str, text: str) -> None:
        prefix = {"you": "You: ", "beacon": "Beacon: ", "note": ""}.get(who, "")
        self.history.configure(state="normal")
        self.history.insert("end", prefix + text + "\n", who)
        self.history.see("end")
        self.history.configure(state="disabled")

    def _submit(self, _event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        threading.Thread(target=self.on_typed, args=(text,), daemon=True).start()


_ = ctypes
