@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-data.txt
if errorlevel 1 goto failed
echo.
echo Data tools installed. To view commands:
echo .venv\Scripts\python.exe scripts\download_videos.py --help
echo .venv\Scripts\python.exe scripts\prepare_clips.py --help
echo.
echo Some platforms require login or an additional JavaScript runtime.
echo The collector records such failures and does not use browser cookies.
pause
exit /b 0
:failed
echo.
echo Setup failed. Check the message above; Python 3.10 or newer is required.
pause
exit /b 1
