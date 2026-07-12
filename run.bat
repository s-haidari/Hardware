@echo off
rem ===========================================================================
rem Run the KiCad Library Manager UI straight from source - no build, no exe.
rem Windows twin of run.sh. Double-click it, or from cmd:
rem     run.bat            the barebones functionality UI (default)
rem     run.bat --full     the redesign shell
rem Any extra args are forwarded to "python -m ui".
rem Portable: paths derive from this file's folder (%~dp0), no hard-coded users.
rem ===========================================================================
setlocal
set "ROOT=%~dp0"

rem The ui package lives under tools\, so put it on the import path - this is the
rem bit that otherwise makes "python -m ui" fail from the repo root.
set "PYTHONPATH=%ROOT%tools;%PYTHONPATH%"

rem Prefer the project-local Windows venv; else the py launcher; else plain python.
if exist "%ROOT%.venv\Scripts\python.exe" (
    "%ROOT%.venv\Scripts\python.exe" -m ui %*
    goto :after
)
echo [run.bat] No Windows .venv found - using Python on PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m ui %*
) else (
    python -m ui %*
)

:after
if errorlevel 1 (
    echo.
    echo [run.bat] The app exited with an error ^(see messages above^).
    echo           If a module is missing ^(e.g. PyQt5^), create a Windows venv:
    echo               py -3 -m venv .venv
    echo               .venv\Scripts\python -m pip install PyQt5 watchdog
    echo           then run this again.
    pause
)
endlocal
