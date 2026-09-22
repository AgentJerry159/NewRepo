@echo off
setlocal
set "APP_DIR=%~dp0"

rem Prefer Conda's base Python: this works before the analysis environment exists.
where conda >nul 2>nul
if not errorlevel 1 (
    call conda run -n base pythonw "%APP_DIR%setup_assistant.py"
    exit /b %errorlevel%
)

rem Fallback for a regular Python installation with Tkinter included.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 "%APP_DIR%setup_assistant.py"
    exit /b %errorlevel%
)

echo Cannot find Conda or Python 3.11.
echo Please install Miniconda or Anaconda first, then double-click this file again.
pause
exit /b 1
