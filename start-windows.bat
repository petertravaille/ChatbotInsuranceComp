@echo off
REM Double-click this file on Windows to start the PT Health Assistant demo.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add to PATH"^) and try again.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting things up ^(takes a minute^)...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || goto :error

echo Starting... the demo opens in your browser. Close this window to stop it.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:5000"
python app\server.py
goto :eof

:error
echo Something went wrong during setup. See the messages above.
pause
