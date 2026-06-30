@echo off
rem Run the Hardware app as a script (serves UI + API, opens the browser).
rem Double-click this, or run it from a terminal.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] backend venv not found at "%ROOT%backend\.venv".
    echo Create it:  python -m venv "%ROOT%backend\.venv"
    echo Then:       "%PY%" -m pip install -r "%ROOT%backend\requirements.txt"
    pause
    exit /b 1
)
"%PY%" "%ROOT%serve.py"
endlocal
