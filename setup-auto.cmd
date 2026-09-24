@echo off
cd /d "%~dp0"
python scripts\setup_auto.py
if errorlevel 1 (
  echo.
  echo Setup failed. Please check the message above and retry.
  pause
  exit /b 1
)
echo.
echo Setup complete. Open start-privacy.cmd to use automatic masking.
pause
