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

rem Miniconda's default installation is often not added to PATH.
set "CONDA_EXE="
for %%P in ("%USERPROFILE%\miniconda3\Scripts\conda.exe" "%USERPROFILE%\anaconda3\Scripts\conda.exe" "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe" "%PROGRAMDATA%\miniconda3\Scripts\conda.exe") do (
    if not defined CONDA_EXE if exist "%%~fP" set "CONDA_EXE=%%~fP"
)
if defined CONDA_EXE (
    call "%CONDA_EXE%" run -n base pythonw "%APP_DIR%setup_assistant.py"
    exit /b %errorlevel%
)

rem Fallback for a regular Python installation with Tkinter included.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 "%APP_DIR%setup_assistant.py"
    exit /b %errorlevel%
)

echo Cannot find Conda or Python 3.11.
echo Opening the required Miniconda installer in your browser.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('本电脑尚未安装运行本工具所需的 Conda。接下来会下载唯一需要的版本：Miniconda（Windows x86_64，64 位）。\n\n请在安装器中选择 Just Me，保持默认安装路径即可；不需要选择 Kilo，也不需要安装 Anaconda Distribution。\n\n安装完成后，重新双击本文件。', '需要先安装 Miniconda', 'OK', 'Information')" >nul 2>nul
start "" "https://pro.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
pause
exit /b 1

