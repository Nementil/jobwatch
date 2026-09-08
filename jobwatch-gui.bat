@echo off
REM Double-click launcher for the jobwatch GUI.
REM Uses the project's virtual environment, so nothing needs to be activated first.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run setup first:
    echo    python -m venv .venv
    echo    .venv\Scripts\python -m pip install -e ".[dev]"
    echo    .venv\Scripts\python -m playwright install chromium
    pause
    exit /b 1
)
if not exist "config.yaml" copy /y "config.example.yaml" "config.yaml" >nul
REM pythonw, not python: no console window behind the GUI.
start "" ".venv\Scripts\pythonw.exe" -m jobwatch gui
