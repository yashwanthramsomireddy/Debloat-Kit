@echo off
title DebloatKit -- Installer Builder
color 0A

echo.
echo  =============================================
echo    DebloatKit -- Inno Setup Installer Builder
echo  =============================================
echo.

REM Check dist\DebloatKit.exe exists
if not exist "dist\DebloatKit.exe" (
    echo  [ERROR] dist\DebloatKit.exe not found.
    echo          Run build.bat first.
    echo.
    pause & exit /b 1
)

REM Find Inno Setup
set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC=C:\Program Files\Inno Setup 6\ISCC.exe

if not defined ISCC (
    echo  [ERROR] Inno Setup 6 not found.
    echo          Download from: https://jrsoftware.org/isinfo.php
    echo.
    pause & exit /b 1
)

echo  [*] Inno Setup: %ISCC%
echo  [*] Building installer...
echo.

"%ISCC%" "DebloatKit_Installer.iss"

if errorlevel 1 (
    echo.
    echo  [ERROR] Installer build failed.
    pause & exit /b 1
)

echo.
echo  =============================================
echo    INSTALLER BUILT SUCCESSFULLY!
echo    Output: installer_output\DebloatKit_Setup_v2.0.exe
echo  =============================================
echo.
pause
