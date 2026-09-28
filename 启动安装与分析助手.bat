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
rem Load PresentationFramework explicitly. The previous version suppressed its
rem load failure, then opened the downloader unconditionally without any prompt.
powershell -NoProfile -STA -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; $nl=[Environment]::NewLine; $msg='本电脑尚未安装运行本工具所需的 Conda。'+$nl+$nl+'需要安装的版本：Miniconda（Windows x86_64，64 位）。'+$nl+'不需要选择 Kilo，也不需要安装 Anaconda Distribution。'+$nl+$nl+'安装时选择 Just Me，并保持默认安装路径。'+$nl+$nl+'现在打开官方下载页吗？'; $answer=[System.Windows.MessageBox]::Show($msg, '需要先安装 Miniconda', 'YesNo', 'Information'); if ($answer -eq 'Yes') { Start-Process 'https://pro.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe' }"
exit /b 1
