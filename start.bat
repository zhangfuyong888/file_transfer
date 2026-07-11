@echo off
chcp 65001 >nul
:: ============================================================
:: LAN File Transfer - Windows Launcher
:: Double-click this file to start.
:: ============================================================
cd /d "%~dp0"

:: ---- Check Python ----
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Need Python 3. Install from https://www.python.org/
    pause
    exit /b 1
)

:: ---- Install Flask if needed ----
python -c "import flask" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Installing Flask...
    pip install flask --quiet
)

:: ---- Start server ----
echo.
echo ============================================================
echo   LAN File Transfer - Starting...
echo ============================================================
echo.
python transfer.py %*

pause
