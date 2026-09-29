"""There He Is - color finder. Magnifier follows cursor/target (never captures itself)."""
import sys, os, time, threading, queue, math, traceback, subprocess
from dataclasses import dataclass

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, colorchooser
except Exception as e:
    print("tkinter failed:", e)
    input("Press Enter to close...")
    sys.exit(1)

def _try_imports():
    missing, mods = [], {}
    for name, pip_name in [
        ("numpy", "numpy"), ("mss", "mss"), ("PIL", "Pillow"),
        ("pyttsx3", "pyttsx3"), ("win32api", "pywin32"),
        ("win32con", "pywin32"), ("win32gui", "pywin32"),
    ]:
        try:
            mods[name] = __import__(name)
        except Exception:
            if pip_name not in missing:
                missing.append(pip_name)
            mods[name] = None
    return mods, missing

MODS, MISSING = _try_imports()
np = MODS.get("numpy")
mss_mod = MODS.get("mss")
PIL = MODS.get("PIL")
pyttsx3 = MODS.get("pyttsx3")
win32api, win32con, win32gui = MODS.get("win32api"), MODS.get("win32con"), MODS.get("win32gui")
WINDOWS = all(x is not None for x in (win32api, win32con, win32gui))
Image = ImageTk = None
if PIL is not None:
    try:
        from PIL import Image, ImageTk
    except Exception:
        Image = ImageTk = None

@dataclass
class Detection:
    x: int; y: int; w: int; h: int; cx: int; cy: int
    aim_x: int; aim_y: int; pixel_count: int; timestamp: float

def log_print(*a):
    print(*a, flush=True)

def grab_rgb(sct, left, top, width, height):
    """Capture a screen rect as an RGB numpy array. Raises on failure."""
    if sct is None:
        raise RuntimeError("mss not initialized")
    if width < 1 or height < 1:
        raise RuntimeError(f"bad grab size {width}x{height}")
    shot = sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
    arr = np.array(shot)
    if arr.size == 0:
        raise RuntimeError("empty screenshot")
    # mss is BGRA
    rgb = arr[:, :, :3][:, :, ::-1].copy()
    return rgb

def find_color_bbox(img, target_rgb, tolerance=30.0, min_pixels=25):
    if np is None or img is None or getattr(img, "size", 0) == 0:
        return None
    r, g, b = target_rgb
    diff = img.astype(np.float32) - np.array([r, g, b], dtype=np.float32)
    dist = (diff * diff).sum(axis=2) ** 0.5
    ys, xs = np.where(dist <= tolerance)
    if len(xs) < min_pixels:
        return None
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    w, h = x2 - x1 + 1, y2 - y1 + 1
    cx, cy = int(xs.mean()), int(ys.mean())
    return Detection(x1, y1, w, h, cx, cy, cx, y1 + max(2, h // 8), len(xs), time.time())

def _clickthrough(win):
    if not WINDOWS:
        return
    try:
        win.update_idletasks()
        hwnd = int(win.winfo_id())
        styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE,
            styles | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW,
        )
    except Exception:
        traceback.print_exc()

class Overlay:
    BAR = 4
    def __init__(self, master):
        self.enabled = True
        self.parts = []
        self.label = None
        try:
            for _ in range(4):
                w = tk.Toplevel(master)
                w.overrideredirect(True)
                w.attributes("-topmost", True)
                w.configure(bg="#FF0000")
                w.withdraw(); w.geometry("1x1+0+0")
                _clickthrough(w)
                self.parts.append(w)
            lab = tk.Toplevel(master)
            lab.overrideredirect(True)
            lab.attributes("-topmost", True)
            lab.configure(bg="#1a0000")
            tk.Label(lab, text="THERE HE IS", fg="#FF3333", bg="#1a0000",
                     font=("Segoe UI", 12, "bold")).pack(padx=8, pady=2)
            lab.withdraw(); lab.geometry("1x1+0+0")
            _clickthrough(lab)
            self.label = lab
        except Exception:
            traceback.print_exc()
    def update(self, det):
        if not self.enabled or not self.parts or det is None:
            self.hide(); return
        pad = 5
        x1, y1 = det.x - pad, det.y - pad
        x2, y2 = det.x + det.w + pad, det.y + det.h + pad
        bw = self.BAR
        geos = [
            f"{max(1, x2-x1)}x{bw}+{x1}+{y1}",
            f"{max(1, x2-x1)}x{bw}+{x1}+{y2-bw}",
            f"{bw}x{max(1, y2-y1)}+{x1}+{y1}",
            f"{bw}x{max(1, y2-y1)}+{x2-bw}+{y1}",
        ]
        for win, geo in zip(self.parts, geos):
            try:
                win.geometry(geo); win.deiconify(); win.lift()
            except Exception:
                pass
        if self.label:
            try:
                self.label.geometry(f"+{max(0, det.aim_x-60)}+{max(0, y1-30)}")
                self.label.deiconify(); self.label.lift()
            except Exception:
                pass
    def hide(self):
        for w in self.parts:
            try: w.withdraw()
            except Exception: pass
        if self.label:
            try: self.label.withdraw()
            except Exception: pass
    def destroy(self):
        self.hide()
        for w in self.parts:
            try: w.destroy()
            except Exception: pass
        self.parts = []
        if self.label:
            try: self.label.destroy()
            except Exception: pass
            self.label = None

class Magnifier:
    """Zooms cursor (or lock-on target). Does NOT capture its own window (that was the black image)."""
    ZOOM = 3.0
    REFRESH_MS = 50
    def __init__(self, parent_app):
        self.app = parent_app
        self.enabled = False
        self.root = None
        self.label = None
        self.meta = None
        self._photo = None
        self._drag = {"x": 0, "y": 0}
        self._after_id = None
        self.sct = None
        self.last_err = None
        self.frames = 0
        try:
            if mss_mod: self.sct = mss_mod.mss()
        except Exception as e:
            self.last_err = str(e)
            parent_app.debug(f"magnifier mss init failed: {e}")
        if WINDOWS:
            self.sw = win32api.GetSystemMetrics(0); self.sh = win32api.GetSystemMetrics(1)
        else:
            self.sw = parent_app.root.winfo_screenwidth(); self.sh = parent_app.root.winfo_screenheight()
        self.win_w = max(360, self.sw // 2)
        self.win_h = max(280, self.sh // 2)
    def _cursor(self):
        if WINDOWS:
            return win32api.GetCursorPos()
        return (self.sw // 2, self.sh // 2)
    def show(self):
        if Image is None or ImageTk is None or self.sct is None or np is None:
            messagebox.showwarning("Magnifier", "Needs Pillow, numpy, mss. Run RUN.bat.")
            self.app.debug("magnifier blocked: missing Pillow/numpy/mss")
            return
        if self.root is not None:
            try: self.root.deiconify()
            except Exception: self.root = None
        if self.root is None:
            self.root = tk.Toplevel(self.app.root)
            self.root.title("Magnifier - drag to move viewer (zooms cursor / target)")
            self.root.attributes("-topmost", True); self.root.resizable(True, True)
            x = (self.sw - self.win_w) // 2; y = (self.sh - self.win_h) // 2
            self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")
            frame = tk.Frame(self.root, bg="#111")
            frame.pack(fill="both", expand=True)
            self.meta = tk.Label(frame, text="magnifier starting...", fg="#9f9", bg="#111",
                                 font=("Consolas", 9), anchor="w")
            self.meta.pack(fill="x")
            self.label = tk.Label(frame, bg="#222")
            self.label.pack(fill="both", expand=True)
            for w in (self.root, frame, self.label, self.meta):
                w.bind("<ButtonPress-1>", self._start_drag)
                w.bind("<B1-Motion>", self._on_drag)
            self.root.bind("<Escape>", lambda e: self.hide())
            self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.enabled = True
        self.app.debug("magnifier opened")
        self._schedule()
    def hide(self):
        self.enabled = False
        if self._after_id and self.root:
            try: self.root.after_cancel(self._after_id)
            except Exception: pass
            self._after_id = None
        if self.root:
            try: self.root.withdraw()
            except Exception: pass
        self.app.debug("magnifier hidden")
    def destroy(self):
        self.hide()
        if self.root:
            try: self.root.destroy()
            except Exception: pass
            self.root = None
    def _start_drag(self, e):
        self._drag["x"] = e.x_root; self._drag["y"] = e.y_root
    def _on_drag(self, e):
        if self.root is None: return
        dx = e.x_root - self._drag["x"]; dy = e.y_root - self._drag["y"]
        self._drag["x"] = e.x_root; self._drag["y"] = e.y_root
        try:
            geo = self.root.geometry().split("+")
            self.root.geometry(f"{geo[0]}+{int(geo[1])+dx}+{int(geo[2])+dy}")
        except Exception: pass
    def _schedule(self):
        if not self.enabled or self.root is None: return
        self._update()
        try: self._after_id = self.root.after(self.REFRESH_MS, self._schedule)
        except Exception: self._after_id = None
    def _update(self):
        if not self.enabled or self.root is None or self.sct is None: return
        try:
            if not self.root.winfo_exists(): return
            ww = max(64, self.root.winfo_width())
            wh = max(48, self.root.winfo_height() - 22)
            src_w = max(32, int(ww / self.ZOOM))
            src_h = max(32, int(wh / self.ZOOM))
            det = getattr(self.app, "last_detection", None)
            if det is not None:
                cx, cy = det.aim_x, det.aim_y
                src = "target"
            else:
                cx, cy = self._cursor()
                src = "cursor"
            src_x = int(max(0, min(cx - src_w // 2, self.sw - src_w)))
            src_y = int(max(0, min(cy - src_h // 2, self.sh - src_h)))
            rgb = grab_rgb(self.sct, src_x, src_y, src_w, src_h)
            mean = float(rgb.mean())
            img = Image.fromarray(rgb, "RGB").resize((ww, max(1, wh)), Image.NEAREST)
            # crosshair
            from PIL import ImageDraw
            d = ImageDraw.Draw(img)
            mx, my = ww // 2, wh // 2
            d.line((mx - 18, my, mx + 18, my), fill=(255, 40, 40), width=2)
            d.line((mx, my - 18, mx, my + 18), fill=(255, 40, 40), width=2)
            self._photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self._photo)
            self.frames += 1
            txt = f"{src}  grab {src_w}x{src_h} @ ({src_x},{src_y})  mean={mean:.1f}  zoom={self.ZOOM}x  frames={self.frames}"
            if self.meta: self.meta.config(text=txt)
            if mean < 1.5 and self.frames % 20 == 1:
                self.app.debug(f"magnifier nearly black mean={mean:.2f} {txt}")
            self.last_err = None
        except Exception as e:
            self.last_err = traceback.format_exc()
            self.app.debug("MAGNIFIER ERROR:\n" + self.last_err)
            if self.meta:
                self.meta.config(text=f"ERROR: {e}")

class MouseController:
    def __init__(self):
        self.move_enabled = False; self.click_enabled = False; self.smoothness = 0.35
        self.click_cooldown = 0.35; self.last_click_time = 0.0; self._target = None
        self._lock = threading.Lock(); self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
    def set_target(self, x, y):
        with self._lock: self._target = (float(x), float(y))
    def clear_target(self):
        with self._lock: self._target = None
    def _get(self):
        return win32api.GetCursorPos() if WINDOWS else (0, 0)
    def _set(self, x, y):
        if WINDOWS:
            try: win32api.SetCursorPos((int(x), int(y)))
            except Exception: pass
    def _click(self):
        if not WINDOWS: return
        try:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0); time.sleep(0.02)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        except Exception: pass
    def _loop(self):
        while self._running:
            try:
                if self.move_enabled:
                    with self._lock: t = self._target
                    if t is not None:
                        cx, cy = self._get(); dx, dy = t[0]-cx, t[1]-cy; dist = math.hypot(dx, dy)
                        if dist > 1.5:
                            s = max(self.smoothness, 0.05); self._set(cx+dx*s, cy+dy*s)
                        if self.click_enabled and dist < 12:
                            now = time.time()
                            if now - self.last_click_time >= self.click_cooldown:
                                self._click(); self.last_click_time = now
            except Exception:
                pass
            time.sleep(0.008)
    def stop(self): self._running = False

class Speaker:
    def __init__(self):
        self.engine = None; self.enabled = True; self.last_speak = 0.0; self.cooldown = 2.5
        self._lock = threading.Lock()
        if pyttsx3 is not None:
            try:
                self.engine = pyttsx3.init(); self.engine.setProperty("rate", 175)
            except Exception as e:
                log_print("TTS unavailable:", e); self.engine = None
    def say(self, text, force=False):
        if not self.enabled: return
        now = time.time()
        if not force and now - self.last_speak < self.cooldown: return
        self.last_speak = now
        log_print("[VOICE]", text)
        if self.engine is None: return
        def _run():
            with self._lock:
                try: self.engine.say(text); self.engine.runAndWait()
                except Exception: pass
        threading.Thread(target=_run, daemon=True).start()

def show_missing_and_install(missing):
    root = tk.Tk(); root.title("There He Is - missing packages"); root.geometry("500x300")
    frm = ttk.Frame(root, padding=16); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="These packages are not installed yet:", wraplength=460).pack(anchor="w")
    ttk.Label(frm, text=", ".join(missing), font=("Consolas", 11)).pack(anchor="w", pady=8)
    log = tk.Text(frm, height=8, wrap="word"); log.pack(fill="both", expand=True)
    log.insert("end", "Click Install, then close and run RUN.bat again.\n")
    def install():
        log.insert("end", "Installing...\n"); log.see("end"); root.update()
        try:
            p = subprocess.run([sys.executable, "-m", "pip", "install"] + missing, capture_output=True, text=True, timeout=300)
            log.insert("end", p.stdout + "\n" + p.stderr + "\n")
            log.insert("end", "Done.\n" if p.returncode == 0 else "Install failed. Use RUN.bat.\n")
        except Exception as e:
            log.insert("end", str(e) + "\n")
        log.see("end")
    ttk.Button(frm, text="Install packages now", command=install).pack(fill="x", pady=8)
    ttk.Button(frm, text="Close", command=root.destroy).pack(fill="x")
    root.mainloop()

class ThereHeIsApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("There He Is - Color Finder")
        self.root.geometry("500x860")
        self.root.resizable(True, True)
        self.target_color = (255, 0, 0)
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
        self.last_detection = None
        self.lost_since = None
        self.scan_count = 0
        self.found_count = 0
        self.sct = None
        self._log_lines = []
        try:
            if mss_mod: self.sct = mss_mod.mss()
        except Exception as e:
            log_print("mss init failed:", e)
        self.overlay = None
        self.magnifier = Magnifier(self)
        self.mouse = MouseController()
        self.speaker = Speaker()
        self.cmd_queue = queue.Queue()
        self._build_ui()
        self._update_color_preview()
        self._sync_toggles()
        threading.Thread(target=self._scan_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._process_queue)
        self.root.after(200, self._ensure_overlay)
        self.root.after(400, self._boot_debug)

    def debug(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_print(line)
        self._log_lines.append(line)
        self._log_lines = self._log_lines[-250:]
        try:
            self.debug_text.configure(state="normal")
            self.debug_text.insert("end", line + "\n")
            self.debug_text.see("end")
            if float(self.debug_text.index("end")) > 400:
                self.debug_text.delete("1.0", "80.0")
            self.debug_text.configure(state="disabled")
        except Exception:
            pass

    def _boot_debug(self):
        mons = []
        try:
            mons = list(self.sct.monitors) if self.sct else []
        except Exception as e:
            mons = [str(e)]
        self.debug(
            f"boot windows={WINDOWS} numpy={np is not None} mss={self.sct is not None} "
            f"PIL={Image is not None} pywin32={WINDOWS} missing={MISSING or 'none'}"
        )
        self.debug(f"monitors={mons}")
        if WINDOWS:
            try:
                self.debug(f"cursor={win32api.GetCursorPos()} primary={win32api.GetSystemMetrics(0)}x{win32api.GetSystemMetrics(1)}")
            except Exception as e:
                self.debug(f"cursor query failed: {e}")

    def _ensure_overlay(self):
        if self.overlay is None:
            self.overlay = Overlay(self.root)
            self.overlay.enabled = self.overlay_enabled.get()
            self.overlay.hide()
            self.debug("overlay ready (thin bars, hidden until hit)")

    def _build_ui(self):
        pad = {"padx": 10, "pady": 3}
        frm = ttk.Frame(self.root, padding=10); frm.pack(fill="both", expand=True)
        color_frame = ttk.LabelFrame(frm, text="Target Color", padding=8); color_frame.pack(fill="x", **pad)
        self.color_preview = tk.Canvas(color_frame, width=60, height=40, bg="#FF0000", highlightthickness=1)
        self.color_preview.grid(row=0, column=0, rowspan=2, padx=8, pady=4)
        ttk.Button(color_frame, text="Pick Color (Eyedropper)", command=self._start_eyedropper).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(color_frame, text="Choose from Palette", command=self._choose_color).grid(row=1, column=1, sticky="ew", padx=4)
        self.color_label = ttk.Label(color_frame, text="RGB(255, 0, 0)")
        self.color_label.grid(row=2, column=0, columnspan=2, pady=4)
        set_frame = ttk.LabelFrame(frm, text="Detection Settings", padding=8); set_frame.pack(fill="x", **pad)
        ttk.Label(set_frame, text="Tolerance:").grid(row=0, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=120, variable=self.tolerance, orient="horizontal").grid(row=0, column=1, sticky="ew", padx=4)
        self.tol_label = ttk.Label(set_frame, text="35"); self.tol_label.grid(row=0, column=2)
        self.tolerance.trace_add("write", lambda *_: self.tol_label.config(text=f"{self.tolerance.get():.0f}"))
        ttk.Label(set_frame, text="Min Pixels:").grid(row=1, column=0, sticky="w")
        ttk.Scale(set_frame, from_=5, to=500, variable=self.min_pixels, orient="horizontal").grid(row=1, column=1, sticky="ew", padx=4)
        self.min_label = ttk.Label(set_frame, text="40"); self.min_label.grid(row=1, column=2)
        self.min_pixels.trace_add("write", lambda *_: self.min_label.config(text=str(self.min_pixels.get())))
        ttk.Label(set_frame, text="Scan Interval:").grid(row=2, column=0, sticky="w")
        ttk.Scale(set_frame, from_=0.02, to=0.25, variable=self.scan_interval, orient="horizontal").grid(row=2, column=1, sticky="ew", padx=4)
        set_frame.columnconfigure(1, weight=1)
        tog = ttk.LabelFrame(frm, text="Feature Toggles", padding=8); tog.pack(fill="x", **pad)
        ttk.Checkbutton(tog, text="Move mouse (smooth, no snapping) -> aims at TOP of color",
                        variable=self.move_enabled, command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog, text="LMB click when cursor is over the color",
                        variable=self.click_enabled, command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog, text="Show red-box overlay (thin bars only, not fullscreen)",
                        variable=self.overlay_enabled, command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog, text='Voice announcements ("There he is")',
                        variable=self.tts_enabled, command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog, text="Magnifier (1/4 screen, follows cursor or target)",
                        variable=self.magnifier_enabled, command=self._sync_toggles).pack(anchor="w")
        sm = ttk.Frame(tog); sm.pack(fill="x", pady=(6, 0))
        ttk.Label(sm, text="Smoothness:").pack(side="left")
        ttk.Scale(sm, from_=0.05, to=1.0, variable=self.smoothness, orient="horizontal",
                  command=self._on_smooth_change).pack(side="left", fill="x", expand=True, padx=6)
        self.smooth_label = ttk.Label(sm, text="0.35"); self.smooth_label.pack(side="left")
        self.smoothness.trace_add("write", lambda *_: self.smooth_label.config(text=f"{self.smoothness.get():.2f}"))
        self.start_btn = ttk.Button(frm, text="START SCANNING", command=self._toggle_running)
        self.start_btn.pack(fill="x", ipady=8, pady=6)
        status = ttk.LabelFrame(frm, text="Live Status", padding=8); status.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="Ready. Pick a color and press Start.")
        ttk.Label(status, textvariable=self.status_var, wraplength=460, justify="left").pack(anchor="w")
        self.detail_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.detail_var, foreground="#555").pack(anchor="w")
        dbg = ttk.LabelFrame(frm, text="Scan / Error Debug", padding=6)
        dbg.pack(fill="both", expand=True, **pad)
        self.debug_text = tk.Text(dbg, height=10, wrap="word", bg="#111", fg="#cfc",
                                  insertbackground="#cfc", font=("Consolas", 8))
        self.debug_text.pack(fill="both", expand=True)
        self.debug_text.configure(state="disabled")
        if not WINDOWS:
            self.detail_var.set("Windows APIs not loaded. Mouse move / click need pywin32 (RUN.bat).")

    def _sync_toggles(self):
        self.mouse.move_enabled = self.move_enabled.get()
        self.mouse.click_enabled = self.click_enabled.get()
        self.speaker.enabled = self.tts_enabled.get()
        if self.overlay is not None:
            self.overlay.enabled = self.overlay_enabled.get()
            if not self.overlay.enabled: self.overlay.hide()
        if not self.move_enabled.get(): self.mouse.clear_target()
        if self.magnifier_enabled.get(): self.magnifier.show()
        else: self.magnifier.hide()

    def _on_smooth_change(self, *_):
        self.mouse.smoothness = float(self.smoothness.get())

    def _update_color_preview(self):
        self.color_preview.config(bg="#%02x%02x%02x" % self.target_color)
        self.color_label.config(text=f"RGB{self.target_color}")

    def _choose_color(self):
        c = colorchooser.askcolor(color=self.target_color, title="Choose Target Color")
        if c and c[0]:
            self.target_color = tuple(int(v) for v in c[0])
            self._update_color_preview()
            self.debug(f"palette color {self.target_color}")

    def _start_eyedropper(self):
        if self.sct is None or Image is None:
            messagebox.showwarning("Eyedropper", "Need mss + Pillow. Run RUN.bat first.")
            self.debug("eyedropper blocked: missing mss/Pillow")
            return
        self.status_var.set("Eyedropper - put the mouse on the color, then press OK")
        self.root.withdraw(); self.root.after(150, self._do_eyedropper)

    def _do_eyedropper(self):
        def sample():
            try:
                x, y = win32api.GetCursorPos() if WINDOWS else (0, 0)
                rgb = grab_rgb(self.sct, x, y, 1, 1)
                p = rgb[0, 0]
                self.target_color = (int(p[0]), int(p[1]), int(p[2]))
                self._update_color_preview()
                self.status_var.set(f"Sampled RGB{self.target_color}. Ready.")
                self.debug(f"eyedropper @({x},{y}) -> {self.target_color}")
            except Exception:
                tb = traceback.format_exc()
                self.status_var.set("Eyedropper failed")
                self.debug("EYEDROPPER ERROR:\n" + tb)
            self.root.deiconify()
        messagebox.showinfo("Eyedropper", "Move your mouse over the desired color and press OK.")
        sample()

    def _toggle_running(self):
        if self.sct is None or np is None:
            messagebox.showwarning("Cannot scan", "Packages missing. Double-click RUN.bat in this folder.")
            self.debug("scan blocked: mss or numpy missing")
            return
        self.running = not self.running
        if self.running:
            self.start_btn.config(text="STOP SCANNING")
            self.status_var.set("Scanning... looking for the color.")
            self.debug(f"SCAN START color={self.target_color} tol={self.tolerance.get():.0f} minpx={self.min_pixels.get()}")
            self.speaker.say("Scanning started", force=True)
        else:
            self.start_btn.config(text="START SCANNING")
            if self.overlay: self.overlay.hide()
            self.mouse.clear_target()
            self.status_var.set("Stopped.")
            self.debug(f"SCAN STOP after {self.scan_count} frames, {self.found_count} hits")
            self.speaker.say("Scanning stopped", force=True)

    def _scan_loop(self):
        last_log = 0.0
        while True:
            if not self.running or self.sct is None or np is None:
                time.sleep(0.05); continue
            t0 = time.time()
            try:
                mon = self.sct.monitors[1]
                rgb = grab_rgb(self.sct, mon["left"], mon["top"], mon["width"], mon["height"])
                self.scan_count += 1
                det = find_color_bbox(rgb, self.target_color, self.tolerance.get(), self.min_pixels.get())
                ms = (time.time() - t0) * 1000
                if det:
                    det.x += mon["left"]; det.y += mon["top"]
                    det.cx += mon["left"]; det.cy += mon["top"]
                    det.aim_x += mon["left"]; det.aim_y += mon["top"]
                    self.found_count += 1
                    self.cmd_queue.put(("found", det, ms, rgb.shape, mon))
                else:
                    self.cmd_queue.put(("lost", None, ms, rgb.shape, mon))
                now = time.time()
                if now - last_log > 2.0:
                    last_log = now
                    mean = float(rgb.mean())
                    self.cmd_queue.put(("debug",
                        f"scan#{self.scan_count} {ms:.0f}ms shape={tuple(rgb.shape)} mean={mean:.1f} "
                        f"mon={mon} hits={self.found_count} found={bool(det)}"))
            except Exception:
                self.cmd_queue.put(("error", traceback.format_exc()))
            time.sleep(max(0.001, self.scan_interval.get() - (time.time() - t0)))

    def _process_queue(self):
        try:
            while True:
                item = self.cmd_queue.get_nowait()
                msg = item[0]
                if msg == "found":
                    self._on_found(item[1])
                elif msg == "lost":
                    self._on_lost()
                elif msg == "error":
                    self.status_var.set("Scan error (see debug)")
                    self.debug("SCAN ERROR:\n" + str(item[1]))
                elif msg == "debug":
                    self.debug(item[1])
        except queue.Empty:
            pass
        try: self.root.after(30, self._process_queue)
        except Exception: pass

    def _on_found(self, det):
        prev = self.last_detection
        self.last_detection = det
        self.lost_since = None
        if self.overlay and self.overlay.enabled: self.overlay.update(det)
        elif self.overlay: self.overlay.hide()
        if self.mouse.move_enabled: self.mouse.set_target(det.aim_x, det.aim_y)
        moved = False
        if prev:
            dist = math.hypot(det.aim_x - prev.aim_x, det.aim_y - prev.aim_y)
            if dist > 40:
                moved = True
                self.detail_var.set(f"Moved ~{dist:.0f}px -> aim({det.aim_x},{det.aim_y}) | top of {det.w}x{det.h} px={det.pixel_count}")
            else:
                self.detail_var.set(f"Tracking aim({det.aim_x},{det.aim_y}) | top of {det.w}x{det.h} px={det.pixel_count}")
        else:
            self.detail_var.set(f"Acquired - aiming at TOP ({det.aim_x},{det.aim_y}) | size={det.w}x{det.h} px={det.pixel_count}")
            self.debug(f"HIT bbox=({det.x},{det.y},{det.w},{det.h}) aim=({det.aim_x},{det.aim_y}) px={det.pixel_count}")
        self.status_var.set("THERE HE IS")
        if prev is None or moved:
            self.speaker.say("There he is")

    def _on_lost(self):
        if self.last_detection is not None:
            if self.lost_since is None:
                self.lost_since = time.time()
                self.status_var.set("Color lost... searching")
                self.detail_var.set("Last position tracked. Still looking.")
                self.debug("LOST target")
                self.speaker.say("Lost him")
            elif time.time() - self.lost_since > 3.0:
                self.detail_var.set("Still searching... color gone for a few seconds.")
        if self.overlay: self.overlay.hide()
        self.mouse.clear_target()
        self.last_detection = None

    def _on_close(self):
        self.running = False
        self.mouse.stop()
        self.magnifier.destroy()
        if self.overlay: self.overlay.destroy()
        try: self.root.destroy()
        except Exception: pass
        os._exit(0)

    def run(self):
        self.root.mainloop()

def main():
    try:
        if MISSING:
            show_missing_and_install(MISSING)
            try: messagebox.showinfo("Restart needed", "If install succeeded, close this and double-click RUN.bat again.")
            except Exception: pass
            return
        ThereHeIsApp().run()
    except Exception:
        traceback.print_exc()
        try:
            r = tk.Tk(); r.withdraw(); messagebox.showerror("There He Is crashed", traceback.format_exc())
        except Exception:
            input("Press Enter to close...")

if __name__ == "__main__":
    print("There He Is - Color Finder starting...", flush=True)
    main()
