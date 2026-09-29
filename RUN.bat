@echo off
title There He Is - Color Finder
cd /d "%~dp0"

echo.
echo  There He Is - Color Finder
echo  Installing / updating packages, then starting...
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python is not installed or not on PATH.
  echo Install Python 3.12 from python.org and check "Add Python to PATH".
  pause
  exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"

echo.
echo  Starting There He Is...
echo.

python "%~dp0main.py"
if errorlevel 1 (
  echo.
  echo  The app exited with an error. See the message above.
  pause
)
