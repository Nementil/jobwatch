@echo off
REM Double-click launcher for a headless run. Keeps the window open so the
REM results stay readable instead of flashing past.
cd /d "%~dp0"
if not exist "config.yaml" copy /y "config.example.yaml" "config.yaml" >nul
".venv\Scripts\python.exe" -m jobwatch run
echo.
pause
