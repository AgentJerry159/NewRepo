@echo off
setlocal
start "" /b powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_setup_assistant.ps1"
exit /b 0
