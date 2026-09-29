# There He Is – Color Finder

Windows color finder with overlay, smooth aim, auto-click, and a draggable magnifier.

## Easiest way to run (Windows)

1. Download the zip of this repo (GitHub → Code → Download ZIP)
2. Extract it somewhere, e.g. `Downloads\thereheis-main`
3. Double-click **RUN.bat**

Do **not** run `python.exe` from a Python install folder by itself — that is the interpreter, not this app.

Do **not** run `pip` / `python main.py` from `Downloads` unless `main.py` and `requirements.txt` are in that same folder.

## Manual run

Open PowerShell **in the folder that contains main.py**:

```
python -m pip install -r requirements.txt
python main.py
```

## Features

- Color picker (eyedropper + palette)
- Fast color detection (tolerance / min pixels)
- Red box overlay + “THERE HE IS”
- Smooth mouse aim at the **top** of the color (no snapping)
- LMB click when over the color
- Draggable magnifier (¼ screen, starts centered)
- All features independently toggleable
- Works while games hide / recenter the cursor

## If it crashes on open

- Use **RUN.bat** so packages install first (`mss`, `numpy`, `Pillow`, `pywin32`, `pyttsx3`, `pynput`)
- Missing packages used to crash instantly; the app now shows a window instead
- Double-clicking `python.exe` / `pythonw.exe` is not this app
