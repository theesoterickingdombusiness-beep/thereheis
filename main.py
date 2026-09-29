"""There He Is – crash-safe color finder."""
import sys, os, time, threading, queue, math, traceback, subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, colorchooser
except Exception as e:
    print("tkinter failed:", e)
    input("Press Enter to close...")
    sys.exit(1)

def _try_imports():
    missing, mods = [], {}
    for name, pip_name in [("numpy","numpy"),("mss","mss"),("PIL","Pillow"),("pyttsx3","pyttsx3"),("win32api","pywin32"),("win32con","pywin32"),("win32gui","pywin32")]:
        try:
            mods[name] = __import__(name)
        except Exception:
            if pip_name not in missing:
                missing.append(pip_name)
            mods[name] = None
    return mods, missing

MODS, MISSING = _try_imports()
np = MODS.get("numpy"); mss_mod = MODS.get("mss"); PIL = MODS.get("PIL"); pyttsx3 = MODS.get("pyttsx3")
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
    x: int; y: int; w: int; h: int; cx: int; cy: int; aim_x: int; aim_y: int; pixel_count: int; timestamp: float

def find_color_bbox(img, target_rgb, tolerance=30.0, min_pixels=25):
    if np is None or img is None or getattr(img, "size", 0) == 0:
        return None
    r,g,b = target_rgb
    diff = img.astype(np.float32) - np.array([r,g,b], dtype=np.float32)
    dist = (diff*diff).sum(axis=2)**0.5
    ys, xs = np.where(dist <= tolerance)
    if len(xs) < min_pixels:
        return None
    x1,x2,y1,y2 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    w,h = x2-x1+1, y2-y1+1
    cx, cy = int(xs.mean()), int(ys.mean())
    return Detection(x1,y1,w,h,cx,cy,cx,y1+max(2,h//8),len(xs),time.time())

class Overlay:
    def __init__(self, master):
        self.enabled = True; self.root = None; self.canvas = None
        try:
            self.root = tk.Toplevel(master)
            self.root.title("ThereHeIs Overlay")
            self.root.attributes("-topmost", True)
            try: self.root.attributes("-transparentcolor", "black")
            except Exception: pass
            self.root.overrideredirect(True); self.root.config(bg="black")
            if WINDOWS:
                self.screen_w = win32api.GetSystemMetrics(78); self.screen_h = win32api.GetSystemMetrics(79)
                self.screen_x = win32api.GetSystemMetrics(76); self.screen_y = win32api.GetSystemMetrics(77)
            else:
                self.screen_w = master.winfo_screenwidth(); self.screen_h = master.winfo_screenheight()
                self.screen_x = self.screen_y = 0
            self.root.geometry(f"{self.screen_w}x{self.screen_h}+{self.screen_x}+{self.screen_y}")
            self.canvas = tk.Canvas(self.root, width=self.screen_w, height=self.screen_h, bg="black", highlightthickness=0)
            self.canvas.pack(fill="both", expand=True); self.root.update_idletasks()
            if WINDOWS:
                try:
                    hwnd = int(self.root.winfo_id())
                    styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT)
                except Exception: pass
        except Exception:
            traceback.print_exc(); self.root = None; self.canvas = None
    def update(self, det):
        if not self.enabled or self.canvas is None: return
        try:
            self.canvas.delete("all")
            if det is None: return
            pad=4
            self.canvas.create_rectangle(det.x-pad, det.y-pad, det.x+det.w+pad, det.y+det.h+pad, outline="#FF0000", width=3)
            ax,ay=det.aim_x, det.aim_y
            self.canvas.create_line(ax-12,ay,ax+12,ay,fill="#FF0000",width=2)
            self.canvas.create_line(ax,ay-12,ax,ay+12,fill="#FF0000",width=2)
            self.canvas.create_text(ax, det.y-18, text="THERE HE IS", fill="#FF2222", font=("Segoe UI",14,"bold"), anchor="s")
        except Exception: pass
    def hide(self):
        if self.canvas is not None:
            try: self.canvas.delete("all")
            except Exception: pass
    def destroy(self):
        if self.root is not None:
            try: self.root.destroy()
            except Exception: pass
            self.root = None; self.canvas = None

class Magnifier:
    ZOOM=2.5; REFRESH_MS=40
    def __init__(self, parent_app):
        self.app=parent_app; self.enabled=False; self.root=None; self.label=None; self._photo=None
        self._drag={"x":0,"y":0}; self._after_id=None; self.sct=None
        try:
            if mss_mod: self.sct = mss_mod.mss()
        except Exception: self.sct=None
        if WINDOWS:
            self.sw=win32api.GetSystemMetrics(0); self.sh=win32api.GetSystemMetrics(1)
        else:
            self.sw=parent_app.root.winfo_screenwidth(); self.sh=parent_app.root.winfo_screenheight()
        self.win_w=max(320,self.sw//2); self.win_h=max(240,self.sh//2)
    def show(self):
        if Image is None or ImageTk is None or self.sct is None:
            messagebox.showwarning("Magnifier", "Needs Pillow and mss. Run RUN.bat."); return
        if self.root is not None:
            try: self.root.deiconify()
            except Exception: self.root=None
        if self.root is None:
            self.root=tk.Toplevel(self.app.root); self.root.title("Magnifier – drag me")
            self.root.attributes("-topmost", True); self.root.resizable(True, True)
            x=(self.sw-self.win_w)//2; y=(self.sh-self.win_h)//2
            self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")
            frame=tk.Frame(self.root,bg="#222",bd=2,relief="raised"); frame.pack(fill="both",expand=True)
            self.label=tk.Label(frame,bg="black"); self.label.pack(fill="both",expand=True)
            for w in (self.root,frame,self.label):
                w.bind("<ButtonPress-1>", self._start_drag); w.bind("<B1-Motion>", self._on_drag)
            self.root.bind("<Escape>", lambda e: self.hide()); self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.enabled=True; self._schedule()
    def hide(self):
        self.enabled=False
        if self._after_id and self.root:
            try: self.root.after_cancel(self._after_id)
            except Exception: pass
            self._after_id=None
        if self.root:
            try: self.root.withdraw()
            except Exception: pass
    def destroy(self):
        self.hide()
        if self.root:
            try: self.root.destroy()
            except Exception: pass
            self.root=None
    def _start_drag(self,e):
        self._drag["x"]=e.x_root; self._drag["y"]=e.y_root
    def _on_drag(self,e):
        if self.root is None: return
        dx=e.x_root-self._drag["x"]; dy=e.y_root-self._drag["y"]
        self._drag["x"]=e.x_root; self._drag["y"]=e.y_root
        try:
            geo=self.root.geometry().split("+")
            self.root.geometry(f"{geo[0]}+{int(geo[1])+dx}+{int(geo[2])+dy}")
        except Exception: pass
    def _schedule(self):
        if not self.enabled or self.root is None: return
        self._update()
        try: self._after_id=self.root.after(self.REFRESH_MS, self._schedule)
        except Exception: self._after_id=None
    def _update(self):
        if not self.enabled or self.root is None or self.sct is None: return
        try:
            if not self.root.winfo_exists(): return
            wx,wy=self.root.winfo_x(), self.root.winfo_y()
            ww,wh=max(64,self.root.winfo_width()), max(64,self.root.winfo_height())
            src_w,src_h=max(32,int(ww/self.ZOOM)), max(32,int(wh/self.ZOOM))
            src_x=max(0,min(wx+(ww-src_w)//2, self.sw-src_w))
            src_y=max(0,min(wy+(wh-src_h)//2, self.sh-src_h))
            shot=self.sct.grab({"left":src_x,"top":src_y,"width":src_w,"height":src_h})
            img=Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").resize((ww,wh), Image.NEAREST)
            self._photo=ImageTk.PhotoImage(img); self.label.configure(image=self._photo)
        except Exception: pass

class MouseController:
    def __init__(self):
        self.move_enabled=False; self.click_enabled=False; self.smoothness=0.35
        self.click_cooldown=0.35; self.last_click_time=0.0; self._target=None
        self._lock=threading.Lock(); self._running=True
        threading.Thread(target=self._loop, daemon=True).start()
    def set_target(self,x,y):
        with self._lock: self._target=(float(x),float(y))
    def clear_target(self):
        with self._lock: self._target=None
    def _get(self):
        return win32api.GetCursorPos() if WINDOWS else (0,0)
    def _set(self,x,y):
        if WINDOWS:
            try: win32api.SetCursorPos((int(x),int(y)))
            except Exception: pass
    def _click(self):
        if not WINDOWS: return
        try:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,0,0,0,0); time.sleep(0.02)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,0,0,0,0)
        except Exception: pass
    def _loop(self):
        while self._running:
            try:
                if self.move_enabled:
                    with self._lock: t=self._target
                    if t is not None:
                        cx,cy=self._get(); dx,dy=t[0]-cx, t[1]-cy; dist=math.hypot(dx,dy)
                        if dist>1.5:
                            s=max(self.smoothness,0.05); self._set(cx+dx*s, cy+dy*s)
                        if self.click_enabled and dist<12:
                            now=time.time()
                            if now-self.last_click_time>=self.click_cooldown:
                                self._click(); self.last_click_time=now
            except Exception: pass
            time.sleep(0.008)
    def stop(self): self._running=False

class Speaker:
    def __init__(self):
        self.engine=None; self.enabled=True; self.last_speak=0.0; self.cooldown=2.5; self._lock=threading.Lock()
        if pyttsx3 is not None:
            try:
                self.engine=pyttsx3.init(); self.engine.setProperty("rate",175)
            except Exception as e:
                print("TTS unavailable:", e); self.engine=None
    def say(self, text, force=False):
        if not self.enabled: return
        now=time.time()
        if not force and now-self.last_speak < self.cooldown: return
        self.last_speak=now; print("[VOICE]", text)
        if self.engine is None: return
        def _run():
            with self._lock:
                try: self.engine.say(text); self.engine.runAndWait()
                except Exception: pass
        threading.Thread(target=_run, daemon=True).start()

def show_missing_and_install(missing):
    root=tk.Tk(); root.title("There He Is – missing packages"); root.geometry("500x300")
    frm=ttk.Frame(root,padding=16); frm.pack(fill="both",expand=True)
    ttk.Label(frm,text="These packages are not installed yet (this used to crash on open):",wraplength=460).pack(anchor="w")
    ttk.Label(frm,text=", ".join(missing), font=("Consolas",11)).pack(anchor="w", pady=8)
    log=tk.Text(frm,height=8,wrap="word"); log.pack(fill="both",expand=True)
    log.insert("end","Click Install, then close and run RUN.bat again.\n")
    def install():
        log.insert("end","Installing...\n"); log.see("end"); root.update()
        try:
            p=subprocess.run([sys.executable,"-m","pip","install"]+missing, capture_output=True, text=True, timeout=300)
            log.insert("end", p.stdout+"\n"+p.stderr+"\n")
            log.insert("end", "Done.\n" if p.returncode==0 else "Install failed. Use RUN.bat.\n")
        except Exception as e:
            log.insert("end", str(e)+"\n")
        log.see("end")
    ttk.Button(frm,text="Install packages now", command=install).pack(fill="x", pady=8)
    ttk.Button(frm,text="Close", command=root.destroy).pack(fill="x")
    root.mainloop()

class ThereHeIsApp:
    def __init__(self):
        self.root=tk.Tk(); self.root.title("There He Is – Color Finder"); self.root.geometry("460x720"); self.root.resizable(False,False)
        self.target_color=(255,0,0)
        self.tolerance=tk.DoubleVar(value=35.0); self.min_pixels=tk.IntVar(value=40); self.scan_interval=tk.DoubleVar(value=0.04)
        self.move_enabled=tk.BooleanVar(value=False); self.click_enabled=tk.BooleanVar(value=False)
        self.overlay_enabled=tk.BooleanVar(value=True); self.tts_enabled=tk.BooleanVar(value=True)
        self.magnifier_enabled=tk.BooleanVar(value=False); self.smoothness=tk.DoubleVar(value=0.35)
        self.running=False; self.last_detection=None; self.lost_since=None; self.sct=None
        try:
            if mss_mod: self.sct=mss_mod.mss()
        except Exception as e: print("mss init failed:", e)
        self.overlay=None; self.magnifier=Magnifier(self); self.mouse=MouseController(); self.speaker=Speaker()
        self.cmd_queue=queue.Queue(); self._build_ui(); self._update_color_preview(); self._sync_toggles()
        threading.Thread(target=self._scan_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._process_queue); self.root.after(200, self._ensure_overlay)
    def _ensure_overlay(self):
        if self.overlay is None:
            self.overlay=Overlay(self.root); self.overlay.enabled=self.overlay_enabled.get()
    def _build_ui(self):
        pad={"padx":10,"pady":4}; frm=ttk.Frame(self.root,padding=12); frm.pack(fill="both",expand=True)
        color_frame=ttk.LabelFrame(frm,text="Target Color",padding=8); color_frame.pack(fill="x",**pad)
        self.color_preview=tk.Canvas(color_frame,width=60,height=40,bg="#FF0000",highlightthickness=1)
        self.color_preview.grid(row=0,column=0,rowspan=2,padx=8,pady=4)
        ttk.Button(color_frame,text="Pick Color (Eyedropper)",command=self._start_eyedropper).grid(row=0,column=1,sticky="ew",padx=4)
        ttk.Button(color_frame,text="Choose from Palette",command=self._choose_color).grid(row=1,column=1,sticky="ew",padx=4)
        self.color_label=ttk.Label(color_frame,text="RGB(255, 0, 0)"); self.color_label.grid(row=2,column=0,columnspan=2,pady=4)
        set_frame=ttk.LabelFrame(frm,text="Detection Settings",padding=8); set_frame.pack(fill="x",**pad)
        ttk.Label(set_frame,text="Tolerance:").grid(row=0,column=0,sticky="w")
        ttk.Scale(set_frame,from_=5,to=120,variable=self.tolerance,orient="horizontal").grid(row=0,column=1,sticky="ew",padx=4)
        self.tol_label=ttk.Label(set_frame,text="35"); self.tol_label.grid(row=0,column=2)
        self.tolerance.trace_add("write", lambda *_: self.tol_label.config(text=f"{self.tolerance.get():.0f}"))
        ttk.Label(set_frame,text="Min Pixels:").grid(row=1,column=0,sticky="w")
        ttk.Scale(set_frame,from_=5,to=500,variable=self.min_pixels,orient="horizontal").grid(row=1,column=1,sticky="ew",padx=4)
        self.min_label=ttk.Label(set_frame,text="40"); self.min_label.grid(row=1,column=2)
        self.min_pixels.trace_add("write", lambda *_: self.min_label.config(text=str(self.min_pixels.get())))
        ttk.Label(set_frame,text="Scan Interval:").grid(row=2,column=0,sticky="w")
        ttk.Scale(set_frame,from_=0.02,to=0.25,variable=self.scan_interval,orient="horizontal").grid(row=2,column=1,sticky="ew",padx=4)
        set_frame.columnconfigure(1,weight=1)
        tog=ttk.LabelFrame(frm,text="Feature Toggles",padding=8); tog.pack(fill="x",**pad)
        ttk.Checkbutton(tog,text="Move mouse (smooth, no snapping) → aims at TOP of color",variable=self.move_enabled,command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog,text="LMB click when cursor is over the color",variable=self.click_enabled,command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog,text="Show red-box overlay",variable=self.overlay_enabled,command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog,text='Voice announcements ("There he is")',variable=self.tts_enabled,command=self._sync_toggles).pack(anchor="w")
        ttk.Checkbutton(tog,text="Magnifier (1/4 screen, draggable, starts centered)",variable=self.magnifier_enabled,command=self._sync_toggles).pack(anchor="w")
        sm=ttk.Frame(tog); sm.pack(fill="x",pady=(6,0))
        ttk.Label(sm,text="Smoothness:").pack(side="left")
        ttk.Scale(sm,from_=0.05,to=1.0,variable=self.smoothness,orient="horizontal",command=self._on_smooth_change).pack(side="left",fill="x",expand=True,padx=6)
        self.smooth_label=ttk.Label(sm,text="0.35"); self.smooth_label.pack(side="left")
        self.smoothness.trace_add("write", lambda *_: self.smooth_label.config(text=f"{self.smoothness.get():.2f}"))
        ttk.Button(frm,text="START SCANNING",command=self._toggle_running).pack(fill="x",ipady=8,pady=6)
        self.start_btn=frm.winfo_children()[-1]
        status=ttk.LabelFrame(frm,text="Live Status",padding=8); status.pack(fill="both",expand=True,**pad)
        self.status_var=tk.StringVar(value="Ready. Pick a color and press Start.")
        ttk.Label(status,textvariable=self.status_var,wraplength=400,justify="left").pack(anchor="w")
        self.detail_var=tk.StringVar(value="")
        ttk.Label(status,textvariable=self.detail_var,foreground="#555").pack(anchor="w")
        if not WINDOWS: self.detail_var.set("Windows APIs not loaded. Mouse move / click need pywin32 (RUN.bat).")
    def _sync_toggles(self):
        self.mouse.move_enabled=self.move_enabled.get(); self.mouse.click_enabled=self.click_enabled.get()
        self.speaker.enabled=self.tts_enabled.get()
        if self.overlay is not None:
            self.overlay.enabled=self.overlay_enabled.get()
            if not self.overlay.enabled: self.overlay.hide()
        if not self.move_enabled.get(): self.mouse.clear_target()
        if self.magnifier_enabled.get(): self.magnifier.show()
        else: self.magnifier.hide()
    def _on_smooth_change(self,*_): self.mouse.smoothness=float(self.smoothness.get())
    def _update_color_preview(self):
        self.color_preview.config(bg="#%02x%02x%02x"%self.target_color)
        self.color_label.config(text=f"RGB{self.target_color}")
    def _choose_color(self):
        c=colorchooser.askcolor(color=self.target_color,title="Choose Target Color")
        if c and c[0]:
            self.target_color=tuple(int(v) for v in c[0]); self._update_color_preview()
    def _start_eyedropper(self):
        if self.sct is None or Image is None:
            messagebox.showwarning("Eyedropper","Need mss + Pillow. Run RUN.bat first."); return
        self.status_var.set("Eyedropper – put the mouse on the color, then press OK")
        self.root.withdraw(); self.root.after(150, self._do_eyedropper)
    def _do_eyedropper(self):
        def sample():
            try:
                x,y = win32api.GetCursorPos() if WINDOWS else (0,0)
                shot=self.sct.grab({"left":x,"top":y,"width":1,"height":1})
                img=np.array(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                p=img[0,0]; self.target_color=(int(p[0]),int(p[1]),int(p[2])); self._update_color_preview()
                self.status_var.set(f"Sampled RGB{self.target_color}. Ready.")
            except Exception as e:
                self.status_var.set(f"Eyedropper failed: {e}")
            self.root.deiconify()
        messagebox.showinfo("Eyedropper","Move your mouse over the desired color and press OK.")
        sample()
    def _toggle_running(self):
        if self.sct is None or np is None:
            messagebox.showwarning("Cannot scan","Packages missing. Double-click RUN.bat in this folder."); return
        self.running=not self.running
        if self.running:
            self.start_btn.config(text="STOP SCANNING"); self.status_var.set("Scanning… looking for the color.")
            self.speaker.say("Scanning started", force=True)
        else:
            self.start_btn.config(text="START SCANNING")
            if self.overlay: self.overlay.hide()
            self.mouse.clear_target(); self.status_var.set("Stopped."); self.speaker.say("Scanning stopped", force=True)
    def _scan_loop(self):
        while True:
            if not self.running or self.sct is None or np is None or Image is None:
                time.sleep(0.05); continue
            t0=time.time()
            try:
                mon=self.sct.monitors[1]; shot=self.sct.grab(mon)
                img=np.array(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                det=find_color_bbox(img, self.target_color, self.tolerance.get(), self.min_pixels.get())
                if det:
                    det.x += mon["left"]; det.y += mon["top"]; det.cx += mon["left"]; det.cy += mon["top"]
                    det.aim_x += mon["left"]; det.aim_y += mon["top"]
                    self.cmd_queue.put(("found", det))
                else:
                    self.cmd_queue.put(("lost", None))
            except Exception as e:
                self.cmd_queue.put(("error", str(e)))
            time.sleep(max(0.001, self.scan_interval.get()-(time.time()-t0)))
    def _process_queue(self):
        try:
            while True:
                msg,data=self.cmd_queue.get_nowait()
                if msg=="found": self._on_found(data)
                elif msg=="lost": self._on_lost()
                elif msg=="error": self.status_var.set(f"Error: {data}")
        except queue.Empty: pass
        try: self.root.after(30, self._process_queue)
        except Exception: pass
    def _on_found(self, det):
        prev=self.last_detection; self.last_detection=det; self.lost_since=None
        if self.overlay and self.overlay.enabled: self.overlay.update(det)
        elif self.overlay: self.overlay.hide()
        if self.mouse.move_enabled: self.mouse.set_target(det.aim_x, det.aim_y)
        moved=False
        if prev:
            dist=math.hypot(det.aim_x-prev.aim_x, det.aim_y-prev.aim_y)
            if dist>40:
                moved=True; self.detail_var.set(f"Moved ~{dist:.0f}px → aim({det.aim_x},{det.aim_y}) | top of {det.w}x{det.h}")
            else:
                self.detail_var.set(f"Tracking aim({det.aim_x},{det.aim_y}) | top of {det.w}x{det.h}")
        else:
            self.detail_var.set(f"Acquired – aiming at TOP ({det.aim_x},{det.aim_y}) | size={det.w}x{det.h}")
        self.status_var.set("THERE HE IS")
        if prev is None or moved: self.speaker.say("There he is")
    def _on_lost(self):
        if self.last_detection is not None:
            if self.lost_since is None:
                self.lost_since=time.time(); self.status_var.set("Color lost… searching")
                self.detail_var.set("Last position tracked. Still looking."); self.speaker.say("Lost him")
            elif time.time()-self.lost_since>3.0:
                self.detail_var.set("Still searching… color gone for a few seconds.")
        if self.overlay: self.overlay.hide()
        self.mouse.clear_target(); self.last_detection=None
    def _on_close(self):
        self.running=False; self.mouse.stop(); self.magnifier.destroy()
        if self.overlay: self.overlay.destroy()
        try: self.root.destroy()
        except Exception: pass
        os._exit(0)
    def run(self): self.root.mainloop()

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
            r=tk.Tk(); r.withdraw(); messagebox.showerror("There He Is crashed", traceback.format_exc())
        except Exception:
            input("Press Enter to close...")

if __name__=="__main__":
    print("There He Is – Color Finder starting…")
    main()
