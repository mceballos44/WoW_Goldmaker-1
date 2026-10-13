@echo off
REM Launches WoW Goldmaker. First run creates a virtual environment (.venv).
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
echo Installing / checking dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo Starting WoW Goldmaker - your browser should open automatically.
streamlit run app.py --server.headless false
pause
