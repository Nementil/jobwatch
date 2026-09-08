@echo off
REM Double-click launcher for the jobwatch GUI.
REM Uses the project's virtual environment, so nothing needs to be activated first.
cd /d "%~dp0"
set "STARTUP_LOG=%~dp0jobwatch-startup.log"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run setup first:
    echo    python -m venv .venv
    echo    .venv\Scripts\python -m pip install -e ".[dev]"
    echo    .venv\Scripts\python -m playwright install chromium
    pause
    exit /b 1
)
if not exist "config.yaml" copy /y "config.example.yaml" "config.yaml" >nul

REM PRE-FLIGHT, and the reason it exists is worth stating.
REM
REM The GUI is launched with pythonw.exe, which has no console. That is what
REM stops a black window sitting behind the app, and it also means anything
REM that fails BEFORE the window opens writes a traceback to a stream nobody
REM is reading. A syntax error in cli.py once presented as "double-click does
REM nothing", with the actual message going to a closed handle.
REM
REM So import the module with the CONSOLE interpreter first. It is fast, it
REM fails loudly, and it turns a silent non-launch into a visible error.
".venv\Scripts\python.exe" -c "import jobwatch.gui" 2>"%STARTUP_LOG%"
if errorlevel 1 (
    echo.
    echo jobwatch could not start. The error was:
    echo ----------------------------------------------------------------
    type "%STARTUP_LOG%"
    echo ----------------------------------------------------------------
    echo.
    echo A copy is saved at:
    echo    %STARTUP_LOG%
    echo.
    pause
    exit /b 1
)

REM Import succeeded, so the log holds nothing useful. Remove it rather than
REM leaving a stale file that would be read as evidence of the last failure.
del "%STARTUP_LOG%" 2>nul

REM pythonw, not python: no console window behind the GUI. Failures AFTER
REM this point are caught inside gui.main and written to the same log.
start "" ".venv\Scripts\pythonw.exe" -m jobwatch gui
