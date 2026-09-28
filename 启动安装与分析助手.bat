@echo off
setlocal
set "APP_DIR=%~dp0"

rem Prefer the source assistant when Conda/Python is available.  This keeps the
rem clickable launcher aligned with the checked-in source after an update.
where conda >nul 2>nul
if not errorlevel 1 (
    call conda run -n base pythonw "%APP_DIR%setup_assistant.py"
    if not errorlevel 1 exit /b 0
    set "LAUNCH_METHOD=已找到的 Conda"
    goto :startup_failed
)

rem Miniconda's default installation is often not added to PATH.
set "CONDA_EXE="
for %%P in ("%USERPROFILE%\miniconda3\Scripts\conda.exe" "%USERPROFILE%\anaconda3\Scripts\conda.exe" "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe" "%PROGRAMDATA%\miniconda3\Scripts\conda.exe") do (
    if not defined CONDA_EXE if exist "%%~fP" set "CONDA_EXE=%%~fP"
)
if defined CONDA_EXE (
    call "%CONDA_EXE%" run -n base pythonw "%APP_DIR%setup_assistant.py"
    if not errorlevel 1 exit /b 0
    set "LAUNCH_METHOD=默认位置的 Conda（%CONDA_EXE%）"
    goto :startup_failed
)

rem Fallback for a regular Python installation with Tkinter included.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 "%APP_DIR%setup_assistant.py"
    if not errorlevel 1 exit /b 0
    set "LAUNCH_METHOD=Python 3.11"
    goto :startup_failed
)

echo Cannot find Conda or Python 3.11.
rem Load PresentationFramework explicitly.  The previous version suppressed its
rem load failure, then opened the downloader unconditionally without any prompt.
powershell -NoProfile -STA -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; $nl=[Environment]::NewLine; $msg='本电脑尚未安装运行本工具所需的 Conda。'+$nl+$nl+'需要安装的版本：Miniconda（Windows x86_64，64 位）。'+$nl+'不需要选择 Kilo，也不需要安装 Anaconda Distribution。'+$nl+$nl+'安装时选择 Just Me，并保持默认安装路径。'+$nl+$nl+'现在打开官方下载页吗？'; $answer=[System.Windows.MessageBox]::Show($msg, '需要先安装 Miniconda', 'YesNo', 'Information'); if ($answer -eq 'Yes') { Start-Process 'https://pro.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe' }" 
exit /b 1

:startup_failed
echo.
echo The setup assistant could not be opened with %LAUNCH_METHOD%.
echo Keep this window open and take a screenshot of the error text above.
powershell -NoProfile -STA -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('安装助手没有成功打开。下方黑色窗口已保留完整报错，请截图发给我。'+[Environment]::NewLine+[Environment]::NewLine+'不要关闭该黑色窗口，点击“确定”后再截图。', '安装助手启动失败', 'OK', 'Error')"
pause
exit /b 1

