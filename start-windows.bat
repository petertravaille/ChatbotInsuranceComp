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

REM The API key is asked once and kept in .anthropic_key in this folder (never uploaded to GitHub).
if "%ANTHROPIC_API_KEY%"=="" (
  if not exist .anthropic_key (
    set /p KEY=Paste your Anthropic API key ^(starts with sk-ant-^) and press Enter: 
    call echo %%KEY%%> .anthropic_key
  )
  set /p ANTHROPIC_API_KEY=<.anthropic_key
)

echo Starting... the demo opens in your browser. Close this window to stop it.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:5000"
python app\server.py
goto :eof

:error
echo Something went wrong during setup. See the messages above.
pause
