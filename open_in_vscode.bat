@echo off
REM One double-click: sets up the environment if needed, then opens this folder in VS Code.
cd /d "%~dp0"

call setup_env.bat nopause
if errorlevel 1 (
    pause
    exit /b 1
)

where code >nul 2>nul
if errorlevel 1 (
    echo.
    echo The "code" command was not found. Open VS Code, press Ctrl+Shift+P and run
    echo   Shell Command: Install 'code' command in PATH
    echo or use File ^> Open Folder and pick this folder.
    pause
    exit /b 1
)

code "%~dp0"
