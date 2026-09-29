# There He Is – High-Performance Color Finder

Fast, accurate on-screen color detection tool with smooth aiming, auto-click, and a draggable magnifier.

## Features

- **Color Picker** – eyedropper or palette
- **Specific Color Finder** – vectorized NumPy detection with adjustable tolerance
- **Red Box Overlay** – bright red rectangle + crosshair on the aim point (toggleable)
- **"There He Is"** – TTS + console announcement (toggleable)
- **Smooth Mouse Aim (no snapping)** – eases the cursor toward the target
- **Aims at the TOP of the color** – not the center
- **LMB Click when over color** – automatic left-click once close enough (toggleable)
- **Draggable Magnifier** – ≈ ¼ of the screen, starts centered, fully draggable, live zoom
- **Fullscreen + Hidden Cursor Compatible**
- **Live Status** – continuous commentary
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
| **Magnifier (¼ screen, draggable)** | Opens a live zoomed window (starts centered). Drag it anywhere. Escape closes it. |
| **Smoothness slider** | 0.05 = very slow/smooth · 1.0 = almost instant |
| **START / STOP** | Begin or stop continuous scanning |

## Magnifier

- Size is approximately **one-quarter of the primary screen area** (half width × half height).
- Starts **centered** on the screen.
- **Drag** the window by its title bar or content area to reposition it.
- Continuously shows a zoomed (2.5×) view of the screen region under its center.
- Press **Escape** while focused on the magnifier to hide it, or uncheck the toggle.

## Aiming Behavior

- Aim point is near the **top** of the color blob.
- Mouse movement uses smooth interpolation (no hard snaps).
- When the cursor is within ~12 px of the aim point and the click toggle is on, a left-click is fired (with cooldown).
