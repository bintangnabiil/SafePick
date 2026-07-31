@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_web_windows.ps1"
if errorlevel 1 (
    echo.
    echo [X] Web server gagal dijalankan.
    pause
    exit /b 1
)
endlocal
