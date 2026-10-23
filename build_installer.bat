@echo off
title Building Caustic Overlay installer
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 build_installer.py %*
) else (
    python build_installer.py %*
)
echo.
pause
