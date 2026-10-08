@echo off
REM Launch the BackyUpy desktop GUI using the local virtual environment.
setlocal

set "REPO_ROOT=%~dp0.."
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Virtual environment not found. Run scripts\install.ps1 first.
    exit /b 1
)

"%VENV_PY%" -m backyupy.ui.app %*
endlocal
