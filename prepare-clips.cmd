@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup-data.cmd first.
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" scripts\prepare_clips.py
) else (
  ".venv\Scripts\python.exe" scripts\prepare_clips.py --input "%~1"
)
pause
