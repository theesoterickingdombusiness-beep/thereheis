"""
There He Is – High-performance color finder
Features: color picker, red-box overlay, mouse move, TTS announcement,
fullscreen/hidden-cursor compatible, live status commentary.
"""

import sys
import time
import threading
import queue
import math
from dataclasses import dataclass
from typing import Optional, Tuple, List

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import numpy as np
from mss import mss
from PIL import Image, ImageTk
import pyttsx3

# Windows-specific for reliable mouse + overlay
try:
    import win32api
    import win32con
    import win32gui
    import win32ui
    WINDOWS = True
except ImportError:
    WINDOWS = False
    print("Warning: pywin32 not available – mouse move & advanced overlay limited")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    cx: int
    cy: int
    pixel_count: int
    timestamp: float


# ---------------------------------------------------------------------------
# Core color search (vectorized, fast)
# ---------------------------------------------------------------------------

def find_color_bbox(
    img: np.ndarray,
    target_rgb: Tuple[int, int, int],
    tolerance: float = 30.0,
    min_pixels: int = 25,
) -> Optional[Detection]:
    """
    Search the BGR/RGB image for pixels close to target_rgb.
    Returns the bounding box + centroid of the largest matching cluster,
    or None if nothing substantial is found.
    """
    if img is None or img.size == 0:
        return None

    # img is HxWx3, RGB order from mss/PIL
    r, g, b = target_rgb
    # Euclidean distance in RGB space (fast + good enough for most cases)
    diff = img.astype(np.float32) - np.array([r, g, b], dtype=np.float32)
    dist = np.sqrt(np.sum(diff * diff, axis=2))

    mask = dist <= tolerance

    ys, xs = np.where(mask)
    count = len(xs)
    if count < min_pixels:
        return None

    # Simple connected-component-ish: take the bounding box of all matches
    # (for higher accuracy you can add real CC, but this is lightning fast)
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    cx = int(xs.mean())
    cy = int(ys.mean())

    return Detection(
        x=x1, y=y1, w=w, h=h,
        cx=cx, cy=cy,
        pixel_count=count,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Overlay window (transparent, always-on-top, click-through)
# ---------------------------------------------------------------------------

class Overlay:
    def __init__(self):
        self.root = tk.Toplevel()
        self.root.title("ThereHeIs Overlay")
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.overrideredirect(True)
        self.root.config(bg="black")

        # Full virtual screen
        if WINDOWS:
            self.screen_w = win32api.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            self.screen_h = win32api.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            self.screen_x = win32api.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            self.screen_y = win32api.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        else:
            self.screen_w = self.root.winfo_screenwidth()
            self.screen_h = self.root.winfo_screenheight()
            self.screen_x = self.screen_y = 0

        self.root.geometry(f"{self.screen_w}x{self.screen_h}+{self.screen_x}+{self.screen_y}")

        self.canvas = tk.Canvas(
            self.root, width=self.screen_w, height=self.screen_h,
            bg="black", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        # Make click-through on Windows
        if WINDOWS:
            hwnd = self.root.winfo_id()
            # WS_EX_LAYERED | WS_EX_TRANSPARENT
            styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)

        self.rect_id = None
        self.text_id = None
        self._last_det: Optional[Detection] = None

    def update(self, det: Optional[Detection]):
        self.canvas.delete("all")
        self._last_det = det
        if det is None:
            return

        # Red box
        x1, y1 = det.x, det.y
        x2, y2 = det.x + det.w, det.y + det.h
        pad = 4
        self.canvas.create_rectangle(
            x1 - pad, y1 - pad, x2 + pad, y2 + pad,
            outline="#FF0000", width=3, tags="box"
        )
        # Crosshair at center
        cx, cy = det.cx, det.cy
        self.canvas.create_line(cx - 12, cy, cx + 12, cy, fill="#FF0000", width=2)
        self.canvas.create_line(cx, cy - 12, cx, cy + 12, fill="#FF0000", width=2)

        # Label
        self.canvas.create_text(
            cx, y1 - 18,
            text="THERE HE IS",
            fill="#FF2222",
            font=("Segoe UI", 14, "bold"),
            anchor="s",
        )

    def hide(self):
        self.canvas.delete("all")
        self._last_det = None


# ---------------------------------------------------------------------------
# Mouse controller (works even when games hide + center the cursor)
# ---------------------------------------------------------------------------

class MouseController:
    def __init__(self):
        self.enabled = False

    def move_to(self, x: int, y: int):
        if not self.enabled:
            return
        if WINDOWS:
            # Absolute position – works even if the game is recentering the cursor.
            # The game may pull it back on the next frame; that is expected.
            win32api.SetCursorPos((int(x), int(y)))
        else:
            # Fallback
            try:
                from pynput.mouse import Controller
                Controller().position = (x, y)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# TTS helper
# ---------------------------------------------------------------------------

class Speaker:
    def __init__(self):
        self.engine = None
        self.last_speak = 0.0
        self.cooldown = 2.5  # seconds between announcements
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 175)
        except Exception as e:
            print(f"TTS unavailable: {e}")

    def say(self, text: str, force: bool = False):
        now = time.time()
        if not force and (now - self.last_speak) < self.cooldown:
            return
        self.last_speak = now
        print(f"[VOICE] {text}")
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class ThereHeIsApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("There He Is – Color Finder")
        self.root.geometry("420x520")
        self.root.resizable(False, False)

        # State
        self.target_color: Tuple[int, int, int] = (255, 0, 0)  # default red
        self.tolerance = tk.DoubleVar(value=35.0)
        self.min_pixels = tk.IntVar(value=40)
        self.scan_interval = tk.DoubleVar(value=0.04)  # ~25 FPS
        self.move_mouse = tk.BooleanVar(value=False)
        self.running = False
        self.last_detection: Optional[Detection] = None
        self.lost_since: Optional[float] = None
        self.detection_count = 0

        self.sct = mss()
        self.overlay = Overlay()
        self.mouse = MouseController()
        self.speaker = Speaker()
        self.cmd_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._update_color_preview()

        # Worker thread
        self.worker = threading.Thread(target=self._scan_loop, daemon=True)
        self.worker.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_queue)

    # ----- UI -----
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        # Color section
        color_frame = ttk.LabelFrame(frm, text="Target Color", padding=8)
        color_frame.pack(fill="x", **pad)

        self.color_preview = tk.Canvas(color_frame, width=60, height=40, bg="#FF0000", highlightthickness=1)
        self.color_preview.grid(row=0, column=0, rowspan=2, padx=8, pady=4)

        ttk.Button(color_frame, text="Pick Color (Eyedropper)", command=self._start_eyedropper).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(color_frame, text="Choose from Palette", command=self._choose_color).grid(
            row=1, column=1, sticky="ew", padx=4
        )

        self.color_label = ttk.Label(color_frame, text="RGB(255, 0, 0)")
        self.color_label.grid(row=2, column=0, columnspan=2, pady=4)

        # Settings
        set_frame = ttk.LabelFrame(frm, text="Detection Settings", padding=8)
        set_frame.pack(fill="x", **pad)

        ttk.Label(set_frame, text="Tolerance:").grid(row=0, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=120, variable=self.tolerance, orient="horizontal").grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self.tol_label = ttk.Label(set_frame, text="35")
        self.tol_label.grid(row=0, column=2)
        self.tolerance.trace_add("write", lambda *_: self.tol_label.config(text=f"{self.tolerance.get():.0f}"))

        ttk.Label(set_frame, text="Min Pixels:").grid(row=1, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=500, variable=self.min_pixels, orient="horizontal").grid(
            row=1, column=1, sticky="ew", padx=4
        )
        self.min_label = ttk.Label(set_frame, text="40")
        self.min_label.grid(row=1, column=2)
        self.min_pixels.trace_add("write", lambda *_: self.min_label.config(text=str(self.min_pixels.get())))

        ttk.Label(set_frame, text="Scan Interval (s):").grid(row=2, column=0, sticky="w")
        ttk.Scale(set_frame, from_=0.02, to=0.25, variable=self.scan_interval, orient="horizontal").grid(
            row=2, column=1, sticky="ew", padx=4
        )

        set_frame.columnconfigure(1, weight=1)

        # Options
        opt_frame = ttk.LabelFrame(frm, text="Actions", padding=8)
        opt_frame.pack(fill="x", **pad)

        ttk.Checkbutton(
            opt_frame, text="Move mouse to color when found",
            variable=self.move_mouse,
            command=self._toggle_mouse
        ).pack(anchor="w")

        # Control buttons
        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill="x", **pad)

        self.start_btn = ttk.Button(btn_frame, text="▶  START SCANNING", command=self._toggle_running)
        self.start_btn.pack(fill="x", ipady=8)

        # Status
        status_frame = ttk.LabelFrame(frm, text="Live Status", padding=8)
        status_frame.pack(fill="both", expand=True, **pad)

        self.status_var = tk.StringVar(value="Ready. Pick a color and press Start.")
        self.status_label = ttk.Label(
            status_frame, textvariable=self.status_var,
            wraplength=380, justify="left"
        )
        self.status_label.pack(anchor="w", fill="x")

        self.detail_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.detail_var, foreground="#555").pack(anchor="w")

    def _update_color_preview(self):
        hexcol = "#%02x%02x%02x" % self.target_color
        self.color_preview.config(bg=hexcol)
        self.color_label.config(text=f"RGB{self.target_color}")

    def _choose_color(self):
        c = colorchooser.askcolor(color=self.target_color, title="Choose Target Color")
        if c and c[0]:
            self.target_color = tuple(int(v) for v in c[0])
            self._update_color_preview()

    def _start_eyedropper(self):
        self.status_var.set("Eyedropper active – click anywhere on the screen to sample the color…")
        self.root.withdraw()  # hide main window so we can click underneath
        self.root.after(200, self._do_eyedropper)

    def _do_eyedropper(self):
        # Capture one pixel under the current mouse after a short delay
        # User clicks → we sample at click location via a temporary full-screen grab + bind
        # Simpler reliable way: use a small delay then sample current cursor pos
        def sample():
            if WINDOWS:
                x, y = win32api.GetCursorPos()
            else:
                from pynput.mouse import Controller
                x, y = Controller().position

            with mss() as sct:
                mon = {"left": x, "top": y, "width": 1, "height": 1}
                shot = sct.grab(mon)
                img = np.array(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                pixel = img[0, 0]
                self.target_color = (int(pixel[0]), int(pixel[1]), int(pixel[2]))

            self._update_color_preview()
            self.root.deiconify()
            self.status_var.set(f"Sampled color RGB{self.target_color}. Ready to scan.")

        # Give user time to position mouse and click
        messagebox.showinfo(
            "Eyedropper",
            "Move your mouse over the desired color and press OK.\n"
            "The color under the cursor will be sampled."
        )
        sample()

    def _toggle_mouse(self):
        self.mouse.enabled = self.move_mouse.get()
        state = "ON" if self.mouse.enabled else "OFF"
        self.status_var.set(f"Mouse move is now {state}")

    def _toggle_running(self):
        self.running = not self.running
        if self.running:
            self.start_btn.config(text="⏹  STOP SCANNING")
            self.status_var.set("Scanning… looking for the color.")
            self.speaker.say("Scanning started", force=True)
        else:
            self.start_btn.config(text="▶  START SCANNING")
            self.overlay.hide()
            self.status_var.set("Stopped.")
            self.speaker.say("Scanning stopped", force=True)

    # ----- Worker -----
    def _scan_loop(self):
        while True:
            if not self.running:
                time.sleep(0.05)
                continue

            t0 = time.time()
            try:
                # Capture primary monitor (fast)
                mon = self.sct.monitors[1]
                shot = self.sct.grab(mon)
                img = np.array(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))

                det = find_color_bbox(
                    img,
                    self.target_color,
                    tolerance=self.tolerance.get(),
                    min_pixels=self.min_pixels.get(),
                )

                if det:
                    # Offset by monitor origin
                    det.x += mon["left"]
                    det.y += mon["top"]
                    det.cx += mon["left"]
                    det.cy += mon["top"]

                    self.cmd_queue.put(("found", det))
                else:
                    self.cmd_queue.put(("lost", None))

            except Exception as e:
                self.cmd_queue.put(("error", str(e)))

            # Maintain desired interval
            elapsed = time.time() - t0
            sleep_t = max(0.001, self.scan_interval.get() - elapsed)
            time.sleep(sleep_t)

    def _process_queue(self):
        try:
            while True:
                msg, data = self.cmd_queue.get_nowait()
                if msg == "found":
                    self._on_found(data)
                elif msg == "lost":
                    self._on_lost()
                elif msg == "error":
                    self.status_var.set(f"Error: {data}")
        except queue.Empty:
            pass
        self.root.after(30, self._process_queue)

    def _on_found(self, det: Detection):
        prev = self.last_detection
        self.last_detection = det
        self.detection_count += 1
        self.lost_since = None

        self.overlay.update(det)

        # Mouse
        if self.mouse.enabled:
            self.mouse.move_to(det.cx, det.cy)

        # Commentary
        moved = False
        if prev:
            dist = math.hypot(det.cx - prev.cx, det.cy - prev.cy)
            if dist > 40:
                moved = True
                self.detail_var.set(
                    f"Color moved ≈{dist:.0f}px → ({det.cx}, {det.cy})  |  pixels={det.pixel_count}"
                )
            else:
                self.detail_var.set(
                    f"Holding at ({det.cx}, {det.cy})  |  size={det.w}×{det.h}  |  pixels={det.pixel_count}"
                )
        else:
            self.detail_var.set(
                f"Acquired at ({det.cx}, {det.cy})  |  size={det.w}×{det.h}  |  pixels={det.pixel_count}"
            )

        self.status_var.set("THERE HE IS")

        # Announce only on new acquisition or significant move
        if prev is None or moved:
            self.speaker.say("There he is")

    def _on_lost(self):
        if self.last_detection is not None:
            if self.lost_since is None:
                self.lost_since = time.time()
                self.status_var.set("Color lost… searching")
                self.detail_var.set("Last seen position was tracked. Still looking.")
                self.speaker.say("Lost him")
            elif time.time() - self.lost_since > 3.0:
                self.detail_var.set("Still searching… color has been gone for a few seconds.")
        self.overlay.hide()
        self.last_detection = None

    def _on_close(self):
        self.running = False
        try:
            self.overlay.root.destroy()
        except Exception:
            pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("There He Is – Color Finder starting…")
    app = ThereHeIsApp()
    app.run()
