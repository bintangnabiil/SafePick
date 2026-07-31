@echo off
setlocal
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File ".\scripts\setup_windows.ps1"
if errorlevel 1 (
    echo.
    echo [X] Setup gagal.
    exit /b 1
)
echo.
echo [OK] Setup selesai.
endlocal
