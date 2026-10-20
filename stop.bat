@echo off
rem M7 stop: kill backend/frontend by PID files (see scripts/stop.py)
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\stop.py
) else (
    python scripts\stop.py
)
