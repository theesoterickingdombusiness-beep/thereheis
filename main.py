"""
There He Is – High-performance color finder
Features: color picker, red-box overlay, smooth mouse aim (top of color),
LMB click when over color, no snapping, draggable magnifier (1/4 screen),
fullscreen/hidden-cursor compatible, TTS, live status, fully toggleable.
"""

import sys
import time
import threading
import queue
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import numpy as np
from mss import mss
from PIL import Image, ImageTk
import pyttsx3

try:
    import win32api
    import win32con
    import win32gui
    WINDOWS = True
except ImportError:
    WINDOWS = False
    print("Warning: pywin32 not available – mouse move & advanced overlay limited")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    cx: int
    cy: int
    aim_x: int
    aim_y: int
    pixel_count: int
    timestamp: float


# ---------------------------------------------------------------------------
# Color search
# ---------------------------------------------------------------------------

def find_color_bbox(
    img: np.ndarray,
    target_rgb: Tuple[int, int, int],
    tolerance: float = 30.0,
    min_pixels: int = 25,
) -> Optional[Detection]:
    if img is None or img.size == 0:
        return None

    r, g, b = target_rgb
    diff = img.astype(np.float32) - np.array([r, g, b], dtype=np.float32)
    dist = np.sqrt(np.sum(diff * diff, axis=2))
    mask = dist <= tolerance

    ys, xs = np.where(mask)
    count = len(xs)
    if count < min_pixels:
        return None

    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    cx = int(xs.mean())
    cy = int(ys.mean())
    aim_x = cx
    aim_y = y1 + max(2, h // 8)

    return Detection(
        x=x1, y=y1, w=w, h=h,
        cx=cx, cy=cy,
        aim_x=aim_x, aim_y=aim_y,
        pixel_count=count,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Overlay (red box)
# ---------------------------------------------------------------------------

class Overlay:
    def __init__(self):
        self.root = tk.Toplevel()
        self.root.title("ThereHeIs Overlay")
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.overrideredirect(True)
        self.root.config(bg="black")

        if WINDOWS:
            self.screen_w = win32api.GetSystemMetrics(78)
            self.screen_h = win32api.GetSystemMetrics(79)
            self.screen_x = win32api.GetSystemMetrics(76)
            self.screen_y = win32api.GetSystemMetrics(77)
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

        if WINDOWS:
            hwnd = self.root.winfo_id()
            styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)

        self.enabled = True

    def update(self, det: Optional[Detection]):
        self.canvas.delete("all")
        if not self.enabled or det is None:
            return
        x1, y1 = det.x, det.y
        x2, y2 = det.x + det.w, det.y + det.h
        pad = 4
        self.canvas.create_rectangle(
            x1 - pad, y1 - pad, x2 + pad, y2 + pad,
            outline="#FF0000", width=3
        )
        ax, ay = det.aim_x, det.aim_y
        self.canvas.create_line(ax - 12, ay, ax + 12, ay, fill="#FF0000", width=2)
        self.canvas.create_line(ax, ay - 12, ax, ay + 12, fill="#FF0000", width=2)
        self.canvas.create_text(
            ax, y1 - 18, text="THERE HE IS",
            fill="#FF2222", font=("Segoe UI", 14, "bold"), anchor="s",
        )

    def hide(self):
        self.canvas.delete("all")


# ---------------------------------------------------------------------------
# Draggable Magnifier (≈ 1/4 of screen area, starts centered)
# ---------------------------------------------------------------------------

class Magnifier:
    """
    Always-on-top window that shows a zoomed view of the screen region
    under its center. Size ≈ 1/4 of the primary screen area.
    Fully draggable.
    """

    ZOOM = 2.5
    REFRESH_MS = 33

    def __init__(self, parent_app):
        self.app = parent_app
        self.enabled = False
        self.root = None
        self.label = None
        self._photo = None
        self._drag_data = {"x": 0, "y": 0}
        self._after_id = None
        self.sct = mss()

        if WINDOWS:
            self.sw = win32api.GetSystemMetrics(0)
            self.sh = win32api.GetSystemMetrics(1)
        else:
            self.sw = parent_app.root.winfo_screenwidth()
            self.sh = parent_app.root.winfo_screenheight()

        self.win_w = self.sw // 2
        self.win_h = self.sh // 2

    def show(self):
        if self.root is not None:
            self.root.deiconify()
            self.enabled = True
            self._schedule_update()
            return

        self.root = tk.Toplevel()
        self.root.title("Magnifier – drag me")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(False)
        self.root.resizable(True, True)

        x = (self.sw - self.win_w) // 2
        y = (self.sh - self.win_h) // 2
        self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

        frame = tk.Frame(self.root, bg="#222", bd=2, relief="raised")
        frame.pack(fill="both", expand=True)

        self.label = tk.Label(frame, bg="black")
        self.label.pack(fill="both", expand=True)

        for w in (self.root, frame, self.label):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        self.root.bind("<Escape>", lambda e: self.hide())

        self.enabled = True
        self._schedule_update()

    def hide(self):
        self.enabled = False
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self.root:
            self.root.withdraw()

    def destroy(self):
        self.hide()
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None

    def _start_drag(self, event):
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root

    def _on_drag(self, event):
        if self.root is None:
            return
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
        geo = self.root.geometry().split("+")
        size = geo[0]
        cur_x = int(geo[1])
        cur_y = int(geo[2])
        self.root.geometry(f"{size}+{cur_x + dx}+{cur_y + dy}")

    def _schedule_update(self):
        if not self.enabled or self.root is None:
            return
        self._update_view()
        self._after_id = self.root.after(self.REFRESH_MS, self._schedule_update)

    def _update_view(self):
        if not self.enabled or self.root is None or not self.root.winfo_exists():
            return
        try:
            wx = self.root.winfo_x()
            wy = self.root.winfo_y()
            ww = max(64, self.root.winfo_width())
            wh = max(64, self.root.winfo_height())

            src_w = max(32, int(ww / self.ZOOM))
            src_h = max(32, int(wh / self.ZOOM))
            src_x = wx + (ww - src_w) // 2
            src_y = wy + (wh - src_h) // 2

            src_x = max(0, min(src_x, self.sw - src_w))
            src_y = max(0, min(src_y, self.sh - src_h))

            mon = {"left": src_x, "top": src_y, "width": src_w, "height": src_h}
            shot = self.sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img = img.resize((ww, wh), Image.NEAREST)

            self._photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self._photo)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Smooth mouse + LMB
# ---------------------------------------------------------------------------

class MouseController:
    def __init__(self):
        self.move_enabled = False
        self.click_enabled = False
        self.smoothness = 0.35
        self.click_cooldown = 0.35
        self.last_click_time = 0.0
        self._target: Optional[Tuple[float, float]] = None
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._smooth_loop, daemon=True)
        self._thread.start()

    def set_target(self, x: float, y: float):
        with self._lock:
            self._target = (float(x), float(y))

    def clear_target(self):
        with self._lock:
            self._target = None

    def _get_cursor(self) -> Tuple[int, int]:
        if WINDOWS:
            return win32api.GetCursorPos()
        try:
            from pynput.mouse import Controller
            return Controller().position
        except Exception:
            return (0, 0)

    def _set_cursor(self, x: int, y: int):
        if WINDOWS:
            win32api.SetCursorPos((int(x), int(y)))
        else:
            try:
                from pynput.mouse import Controller
                Controller().position = (x, y)
            except Exception:
                pass

    def _click_lmb(self):
        if not WINDOWS:
            return
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _smooth_loop(self):
        while self._running:
            if self.move_enabled:
                with self._lock:
                    target = self._target
                if target is not None:
                    cx, cy = self._get_cursor()
                    tx, ty = target
                    dx = tx - cx
                    dy = ty - cy
                    dist = math.hypot(dx, dy)
                    if dist > 1.5:
                        step = max(self.smoothness, 0.05)
                        self._set_cursor(cx + dx * step, cy + dy * step)
                    if self.click_enabled and dist < 12:
                        now = time.time()
                        if now - self.last_click_time >= self.click_cooldown:
                            self._click_lmb()
                            self.last_click_time = now
            time.sleep(0.008)

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

class Speaker:
    def __init__(self):
        self.engine = None
        self.enabled = True
        self.last_speak = 0.0
        self.cooldown = 2.5
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 175)
        except Exception as e:
            print(f"TTS unavailable: {e}")

    def say(self, text: str, force: bool = False):
        if not self.enabled:
            return
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
# Main App
# ---------------------------------------------------------------------------

class ThereHeIsApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("There He Is – Color Finder")
        self.root.geometry("440x680")
        self.root.resizable(False, False)

        self.target_color: Tuple[int, int, int] = (255, 0, 0)
        self.tolerance = tk.DoubleVar(value=35.0)
        self.min_pixels = tk.IntVar(value=40)
        self.scan_interval = tk.DoubleVar(value=0.04)

        self.move_enabled = tk.BooleanVar(value=False)
        self.click_enabled = tk.BooleanVar(value=False)
        self.overlay_enabled = tk.BooleanVar(value=True)
        self.tts_enabled = tk.BooleanVar(value=True)
        self.magnifier_enabled = tk.BooleanVar(value=False)
        self.smoothness = tk.DoubleVar(value=0.35)

        self.running = False
        self.last_detection: Optional[Detection] = None
        self.lost_since: Optional[float] = None
        self.detection_count = 0

        self.sct = mss()
        self.overlay = Overlay()
        self.magnifier = Magnifier(self)
        self.mouse = MouseController()
        self.speaker = Speaker()
        self.cmd_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._update_color_preview()
        self._sync_toggles()

        self.worker = threading.Thread(target=self._scan_loop, daemon=True)
        self.worker.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._process_queue)

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        color_frame = ttk.LabelFrame(frm, text="Target Color", padding=8)
        color_frame.pack(fill="x", **pad)
        self.color_preview = tk.Canvas(color_frame, width=60, height=40, bg="#FF0000", highlightthickness=1)
        self.color_preview.grid(row=0, column=0, rowspan=2, padx=8, pady=4)
        ttk.Button(color_frame, text="Pick Color (Eyedropper)", command=self._start_eyedropper).grid(
            row=0, column=1, sticky="ew", padx=4)
        ttk.Button(color_frame, text="Choose from Palette", command=self._choose_color).grid(
            row=1, column=1, sticky="ew", padx=4)
        self.color_label = ttk.Label(color_frame, text="RGB(255, 0, 0)")
        self.color_label.grid(row=2, column=0, columnspan=2, pady=4)

        set_frame = ttk.LabelFrame(frm, text="Detection Settings", padding=8)
        set_frame.pack(fill="x", **pad)
        ttk.Label(set_frame, text="Tolerance:").grid(row=0, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=120, variable=self.tolerance, orient="horizontal").grid(
            row=0, column=1, sticky="ew", padx=4)
        self.tol_label = ttk.Label(set_frame, text="35")
        self.tol_label.grid(row=0, column=2)
        self.tolerance.trace_add("write", lambda *_: self.tol_label.config(text=f"{self.tolerance.get():.0f}"))

        ttk.Label(set_frame, text="Min Pixels:").grid(row=1, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=500, variable=self.min_pixels, orient="horizontal").grid(
            row=1, column=1, sticky="ew", padx=4)
        self.min_label = ttk.Label(set_frame, text="40")
        self.min_label.grid(row=1, column=2)
        self.min_pixels.trace_add("write", lambda *_: self.min_label.config(text=str(self.min_pixels.get())))

        ttk.Label(set_frame, text="Scan Interval:").grid(row=2, column=0, sticky="w")
        ttk.Scale(set_frame, from_=0.02, to=0.25, variable=self.scan_interval, orient="horizontal").grid(
            row=2, column=1, sticky="ew", padx=4)
        set_frame.columnconfigure(1, weight=1)

        tog_frame = ttk.LabelFrame(frm, text="Feature Toggles", padding=8)
        tog_frame.pack(fill="x", **pad)

        ttk.Checkbutton(
            tog_frame, text="Move mouse (smooth, no snapping) → aims at TOP of color",
            variable=self.move_enabled, command=self._sync_toggles
        ).pack(anchor="w")
        ttk.Checkbutton(
            tog_frame, text="LMB click when cursor is over the color",
            variable=self.click_enabled, command=self._sync_toggles
        ).pack(anchor="w")
        ttk.Checkbutton(
            tog_frame, text="Show red-box overlay",
            variable=self.overlay_enabled, command=self._sync_toggles
        ).pack(anchor="w")
        ttk.Checkbutton(
            tog_frame, text="Voice announcements (\"There he is\")",
            variable=self.tts_enabled, command=self._sync_toggles
        ).pack(anchor="w")
        ttk.Checkbutton(
            tog_frame, text="Magnifier (¼ screen, draggable, starts centered)",
            variable=self.magnifier_enabled, command=self._sync_toggles
        ).pack(anchor="w")

        sm_frame = ttk.Frame(tog_frame)
        sm_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(sm_frame, text="Smoothness:").pack(side="left")
        ttk.Scale(
            sm_frame, from_=0.05, to=1.0, variable=self.smoothness,
            orient="horizontal", command=self._on_smooth_change
        ).pack(side="left", fill="x", expand=True, padx=6)
        self.smooth_label = ttk.Label(sm_frame, text="0.35")
        self.smooth_label.pack(side="left")
        self.smoothness.trace_add("write", lambda *_: self.smooth_label.config(
            text=f"{self.smoothness.get():.2f}"))

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btn_frame, text="▶  START SCANNING", command=self._toggle_running)
        self.start_btn.pack(fill="x", ipady=8)

        status_frame = ttk.LabelFrame(frm, text="Live Status", padding=8)
        status_frame.pack(fill="both", expand=True, **pad)
        self.status_var = tk.StringVar(value="Ready. Pick a color and press Start.")
        ttk.Label(status_frame, textvariable=self.status_var, wraplength=400, justify="left").pack(anchor="w")
        self.detail_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.detail_var, foreground="#555").pack(anchor="w")

    def _sync_toggles(self):
        self.mouse.move_enabled = self.move_enabled.get()
        self.mouse.click_enabled = self.click_enabled.get()
        self.overlay.enabled = self.overlay_enabled.get()
        self.speaker.enabled = self.tts_enabled.get()
        if not self.overlay.enabled:
            self.overlay.hide()
        if not self.move_enabled.get():
            self.mouse.clear_target()

        if self.magnifier_enabled.get():
            self.magnifier.show()
        else:
            self.magnifier.hide()

    def _on_smooth_change(self, *_):
        self.mouse.smoothness = float(self.smoothness.get())

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
        self.status_var.set("Eyedropper – position mouse over the color then press OK")
        self.root.withdraw()
        self.root.after(150, self._do_eyedropper)

    def _do_eyedropper(self):
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
            self.status_var.set(f"Sampled RGB{self.target_color}. Ready.")

        messagebox.showinfo(
            "Eyedropper",
            "Move your mouse over the desired color and press OK.\n"
            "The color under the cursor will be sampled."
        )
        sample()

    def _toggle_running(self):
        self.running = not self.running
        if self.running:
            self.start_btn.config(text="⏹  STOP SCANNING")
            self.status_var.set("Scanning… looking for the color.")
            self.speaker.say("Scanning started", force=True)
        else:
            self.start_btn.config(text="▶  START SCANNING")
            self.overlay.hide()
            self.mouse.clear_target()
            self.status_var.set("Stopped.")
            self.speaker.say("Scanning stopped", force=True)

    def _scan_loop(self):
        while True:
            if not self.running:
                time.sleep(0.05)
                continue
            t0 = time.time()
            try:
                mon = self.sct.monitors[1]
                shot = self.sct.grab(mon)
                img = np.array(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                det = find_color_bbox(
                    img, self.target_color,
                    tolerance=self.tolerance.get(),
                    min_pixels=self.min_pixels.get(),
                )
                if det:
                    det.x += mon["left"]
                    det.y += mon["top"]
                    det.cx += mon["left"]
                    det.cy += mon["top"]
                    det.aim_x += mon["left"]
                    det.aim_y += mon["top"]
                    self.cmd_queue.put(("found", det))
                else:
                    self.cmd_queue.put(("lost", None))
            except Exception as e:
                self.cmd_queue.put(("error", str(e)))
            elapsed = time.time() - t0
            time.sleep(max(0.001, self.scan_interval.get() - elapsed))

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

        if self.overlay.enabled:
            self.overlay.update(det)
        else:
            self.overlay.hide()

        if self.mouse.move_enabled:
            self.mouse.set_target(det.aim_x, det.aim_y)

        moved = False
        if prev:
            dist = math.hypot(det.aim_x - prev.aim_x, det.aim_y - prev.aim_y)
            if dist > 40:
                moved = True
                self.detail_var.set(
                    f"Moved ≈{dist:.0f}px → aim({det.aim_x},{det.aim_y})  |  top of {det.w}×{det.h}  |  px={det.pixel_count}"
                )
            else:
                self.detail_var.set(
                    f"Tracking aim({det.aim_x},{det.aim_y})  |  top of {det.w}×{det.h}  |  px={det.pixel_count}"
                )
        else:
            self.detail_var.set(
                f"Acquired – aiming at TOP ({det.aim_x},{det.aim_y})  |  size={det.w}×{det.h}"
            )

        self.status_var.set("THERE HE IS")
        if prev is None or moved:
            self.speaker.say("There he is")

    def _on_lost(self):
        if self.last_detection is not None:
            if self.lost_since is None:
                self.lost_since = time.time()
                self.status_var.set("Color lost… searching")
                self.detail_var.set("Last position tracked. Still looking.")
                self.speaker.say("Lost him")
            elif time.time() - self.lost_since > 3.0:
                self.detail_var.set("Still searching… color gone for a few seconds.")
        self.overlay.hide()
        self.mouse.clear_target()
        self.last_detection = None

    def _on_close(self):
        self.running = False
        self.mouse.stop()
        self.magnifier.destroy()
        try:
            self.overlay.root.destroy()
        except Exception:
            pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("There He Is – Color Finder starting…")
    app = ThereHeIsApp()
    app.run()
