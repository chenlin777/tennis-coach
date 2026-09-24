@echo off
cd /d "%~dp0"
python serve.py --open
if errorlevel 1 pause
