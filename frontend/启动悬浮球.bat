@echo off
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" "frontend\overlay.py"
