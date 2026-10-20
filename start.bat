@echo off
rem M7 one-click start: migrate + backend + frontend (see scripts/serve.py)
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\serve.py %*
) else (
    echo venv not found. Please create venv and install deps first (see README).
    exit /b 1
)
