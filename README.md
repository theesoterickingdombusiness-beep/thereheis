# There He Is – High-Performance Color Finder

Fast, accurate on-screen color detection tool.

## Features
- **Color Picker** – sample any color from the screen with one click
- **Specific Color Finder** – detect a target color with adjustable tolerance
- **Red Box Overlay** – draws a bright red rectangle around the detected color in real time
- **"There He Is"** – announces (TTS + console) when the color is found
- **Mouse Move to Color** – optionally moves the mouse cursor to the center of the detection
- **Fullscreen + Hidden Cursor Compatible** – works even when a game hides and recenters the cursor
- **Live Status** – continuous commentary about where the color is, if it moved, if it was lost, etc.
- High detection rate and accuracy using vectorized NumPy search + optional clustering

## Requirements
- Windows (uses `pywin32` for reliable mouse & overlay)
- Python 3.10+

```bash
pip install -r requirements.txt
python main.py
```

## Controls
- **Pick Color** – activates eyedropper (click anywhere on screen)
- **Start / Stop** – begin or stop continuous scanning
- **Move Mouse** – toggle automatic mouse movement to the found color
- **Tolerance** slider – how strict the color match is
- **Min Size** – ignore tiny noise pixels / require a minimum blob size

## Notes on Fullscreen + Hidden Cursor
When a game hides the cursor and forces it to the center every frame, the system cursor is still movable via the Windows API. This tool uses direct `SetCursorPos` calls so the mouse can still be driven to the target. The game may pull the cursor back on the next frame — this is expected behavior of relative-input games. The red box overlay and detection continue to work regardless.

## Performance
Uses `mss` for low-latency screen capture and NumPy for extremely fast color matching. Detection runs in a background thread so the UI stays responsive.
