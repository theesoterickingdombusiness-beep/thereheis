# There He Is – High-Performance Color Finder

Fast, accurate on-screen color detection tool with smooth aiming and auto-click.

## Features

- **Color Picker** – eyedropper or palette
- **Specific Color Finder** – vectorized NumPy detection with adjustable tolerance
- **Red Box Overlay** – bright red rectangle + crosshair on the aim point (toggleable)
- **"There He Is"** – TTS + console announcement (toggleable)
- **Smooth Mouse Aim (no snapping)** – eases the cursor toward the target instead of teleporting
- **Aims at the TOP of the color** – not the center
- **LMB Click when over color** – automatic left-click once the cursor is close enough (toggleable)
- **Fullscreen + Hidden Cursor Compatible** – uses direct Windows API, works while games hide/recenter the cursor
- **Live Status** – continuous commentary about acquisition, movement, and loss
- **All major features are independently toggleable**

## Requirements

- Windows (uses `pywin32`)
- Python 3.10+

```bash
pip install -r requirements.txt
python main.py
```

## Controls

| Control | Description |
|---------|-------------|
| **Pick Color (Eyedropper)** | Sample any pixel under the mouse |
| **Choose from Palette** | Standard color dialog |
| **Tolerance / Min Pixels / Scan Interval** | Detection sensitivity & speed |
| **Move mouse (smooth, no snapping)** | Toggle smooth aim at the **top** of the detected color |
| **LMB click when cursor is over the color** | Auto left-click once close enough |
| **Show red-box overlay** | Toggle the visual box + crosshair |
| **Voice announcements** | Toggle TTS |
| **Smoothness slider** | 0.05 = very slow/smooth · 1.0 = almost instant |
| **START / STOP** | Begin or stop continuous scanning |

## Aiming Behavior

- The aim point is calculated at the **top** of the color blob (slightly inset from the absolute top edge).
- Mouse movement is **smooth interpolation** – no hard snaps.
- When the cursor gets within ~12 px of the aim point and the click toggle is on, a left mouse button click is performed (with cooldown).

## Fullscreen / Hidden Cursor Notes

Games that hide the cursor and force it to the center every frame still leave the system cursor movable via `SetCursorPos`. This tool drives the cursor smoothly toward the target. The game may pull it back on the next frame — that is expected with relative-input titles. Detection and the red-box overlay continue regardless.

## Performance

`mss` for low-latency capture + NumPy vectorized search running in a background thread. Movement loop runs at ~120 Hz for smooth cursor motion.
