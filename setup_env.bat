@echo off
REM Creates the .venv (first run) and installs/updates dependencies.
REM Double-click it, or run "setup_env.bat nopause" from a task.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
)
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Could not create the virtual environment. Install Python 3.10+ from python.org
    echo and tick "Add python.exe to PATH", then run this again.
    if /i not "%~1"=="nopause" pause
    exit /b 1
)

echo Installing / updating dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo Dependency installation failed.
    if /i not "%~1"=="nopause" pause
    exit /b 1
)

echo.
echo Environment ready.
if /i not "%~1"=="nopause" pause
exit /b 0
