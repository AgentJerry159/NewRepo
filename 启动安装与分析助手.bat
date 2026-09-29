@echo off
setlocal
set "APP_DIR=%~dp0"

rem Prefer the bundled assistant: it contains its own Python runtime and can
rem therefore open even on a brand-new computer with no Python or Conda.
if exist "%APP_DIR%PyFeatSetupAssistant.exe" (
    start "" "%APP_DIR%PyFeatSetupAssistant.exe"
    exit /b 0
)

rem Source-only GitHub downloads do not contain the binary assistant.
rem In that case use the PowerShell fallback, which guides Miniconda setup.
start "" /b powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "%APP_DIR%start_setup_assistant.ps1"
exit /b 0

