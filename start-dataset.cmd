@echo off
cd /d "%~dp0"
python scripts\create_review.py --open
if errorlevel 1 pause
