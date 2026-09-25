@echo off
setlocal
cd /d "%~dp0"

echo [WASTED] Installing build deps...
python -m pip install -U pip pyinstaller PyQt6
python -m pip install -r requirements.txt

echo [WASTED] Building one-file exe...
pyinstaller --noconfirm --clean ^
  --name WASTED ^
  --windowed ^
  --onefile ^
  --noconsole ^
  --hidden-import PyQt6 ^
  --hidden-import discord ^
  --hidden-import discord.ext.commands ^
  --hidden-import nacl ^
  --collect-all PyQt6 ^
  wasted.py

if exist "dist\WASTED.exe" (
  echo.
  echo [WASTED] OK → dist\WASTED.exe
) else (
  echo.
  echo [WASTED] Build failed — check output above.
  exit /b 1
)

endlocal
