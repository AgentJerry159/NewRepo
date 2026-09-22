@echo off
setlocal
set "APP_DIR=%~dp0"

rem Future portable package: start the EXE next to this launcher.
if exist "%APP_DIR%PyFeatFatigue.exe" (
    start "" "%APP_DIR%PyFeatFatigue.exe"
    exit /b 0
)

rem Source-code mode: an explicit interpreter path takes priority.
if defined PYFEAT_PYTHON (
    if exist "%PYFEAT_PYTHON%" (
        "%PYFEAT_PYTHON%" "%APP_DIR%fatigue_pyfeat_debug_gui.py"
        exit /b %errorlevel%
    )
    echo PYFEAT_PYTHON points to a missing Python executable:
    echo %PYFEAT_PYTHON%
    pause
    exit /b 1
)

rem Then try a Conda environment by name, without assuming its drive or install path.
where conda >nul 2>nul
if not errorlevel 1 (
    call conda run -n pyfeat_fatigue pythonw "%APP_DIR%fatigue_pyfeat_debug_gui.py"
    if errorlevel 1 (
        echo Failed to start Conda environment "pyfeat_fatigue".
        echo Set PYFEAT_PYTHON to the environment's pythonw.exe, then try again.
        pause
        exit /b 1
    )
    exit /b 0
)

rem Last fallback for a normally installed Python 3.11 environment.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 "%APP_DIR%fatigue_pyfeat_debug_gui.py"
    exit /b %errorlevel%
)

echo No portable EXE, Conda environment, or Python 3.11 launcher was found.
echo Before packaging, install the required Python environment or set PYFEAT_PYTHON.
pause
exit /b 1
