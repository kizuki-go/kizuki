@echo off
cd /d "%~dp0"

echo === cleanup ===

if exist build          rmdir /s /q build
if exist dist           rmdir /s /q dist
if exist katago\KataGoData  rmdir /s /q katago\KataGoData

for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)

echo === reset settings ===
reg delete "HKCU\Software\Kizuki" /f >nul 2>&1
echo settings cleared.

echo === build start ===
C:\Users\t1914\AppData\Local\Python\pythoncore-3.14-64\Scripts\pyinstaller.exe kizuki.spec

if %errorlevel% neq 0 (
    echo build failed.
    pause
    exit /b 1
)

echo.
echo === build complete ===
echo output: dist\Kizuki\Kizuki.exe
pause
