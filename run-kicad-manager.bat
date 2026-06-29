@echo off
rem ---------------------------------------------------------------------------
rem Launches the KiCad Library Manager GUI using the project-local venv.
rem Portable: %~dp0 is this file's folder (the repo root), so no hard-coded
rem usernames or paths. Double-click this file to run the app.
rem ---------------------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "APP=%ROOT%tools\LibraryManager.py"

if not exist "%PY%" (
    echo [ERROR] venv not found at "%ROOT%.venv".
    echo Create it with:  "C:\Program Files\KiCad\10.0\bin\python.exe" -m venv .venv
    echo Then:            .venv\Scripts\python -m pip install PyQt5 watchdog
    pause
    exit /b 1
)

rem Prefer pythonw (no console window); fall back to python if missing.
if exist "%PYW%" (
    start "" "%PYW%" "%APP%"
) else (
    "%PY%" "%APP%"
)
endlocal
