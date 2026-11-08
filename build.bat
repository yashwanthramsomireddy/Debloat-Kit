@echo off
title DebloatKit -- Build Script
color 0A

echo.
echo  =============================================
echo    DebloatKit v2.0 -- Build Script
echo    TeamExyKings - Yashwanth Ram Somireddy
echo  =============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python 3.11+ not found. https://python.org
    pause & exit /b 1
)
echo  [OK] Python found.

REM Install dependencies
echo  [1/3] Installing dependencies...
pip install customtkinter pyinstaller pillow --quiet
if errorlevel 1 (
    echo  [ERROR] pip install failed.
    pause & exit /b 1
)
echo       Done.

REM Clean previous build artifacts only
echo  [2/3] Cleaning previous build...
if exist "dist\DebloatKit.exe" del /f /q "dist\DebloatKit.exe"
if exist "build" rmdir /s /q "build"
if exist "DebloatKit.spec" del /f /q "DebloatKit.spec"
echo       Done.

REM Build -- assets/icon.ico is embedded, data/ and assets/ are bundled
echo  [3/3] Building DebloatKit.exe...
echo.

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "DebloatKit" ^
    --icon "assets\icon.ico" ^
    --add-data "data;data" ^
    --add-data "assets;assets" ^
    --hidden-import customtkinter ^
    --hidden-import PIL ^
    --hidden-import PIL._tkinter_finder ^
    --collect-all customtkinter ^
    DebloatKit.py

REM NOTE: --uac-admin removed intentionally.
REM The installer (DebloatKit_Installer.iss) uses PrivilegesRequired=lowest
REM so Windows does NOT force UAC on launch. ADB runs fine without elevation.

if errorlevel 1 (
    echo.
    echo  [ERROR] Build failed. See output above.
    pause & exit /b 1
)

echo.
echo  =============================================
echo    BUILD SUCCESSFUL!
echo    Output: dist\DebloatKit.exe
echo  =============================================
echo.
echo  Next: run build_installer.bat to create the setup .exe
echo.
pause
