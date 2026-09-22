@echo off
setlocal
set "APP_DIR=%~dp0"

rem Prefer the small packaged helper when it is distributed beside the source files.
if exist "%APP_DIR%PyFeatSetupAssistant.exe" (
    start "" "%APP_DIR%PyFeatSetupAssistant.exe"
    exit /b 0
)

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
echo Opening the official Miniconda installation instructions in your browser.
echo Install Miniconda or Anaconda first, then double-click this file again.
start "" "https://docs.anaconda.com/miniconda/"
pause
exit /b 1

