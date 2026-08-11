@echo off
cd /d "%~dp0"
title PDF Voice Reader
echo ========================================
echo    PDF Voice Reader - Starting...
echo ========================================
echo.
venv\Scripts\python.exe main.py
if errorlevel 1 (
    echo.
    echo ========================================
    echo    Failed! Please install dependencies:
    echo    venv\Scripts\pip.exe install -r requirements.txt
    echo ========================================
    pause
)
