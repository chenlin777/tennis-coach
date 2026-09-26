@echo off
cd /d "%~dp0"
python serve.py --page forehand --open
if errorlevel 1 pause
