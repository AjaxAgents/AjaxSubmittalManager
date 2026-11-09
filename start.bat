@echo off
REM Ajax Submittal Manager - one-click start for Windows.
REM First run creates a virtual environment and installs dependencies (needs Python 3.11+ on PATH).
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1)
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r backend\requirements.txt || (echo Dependency install failed. & pause & exit /b 1)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env - add your Anthropic API key there, or in the app under Settings.
)

if not exist "frontend\dist\index.html" (
  echo The prebuilt frontend is missing. Build it with: cd frontend ^&^& npm install ^&^& npm run build
)

cd backend
python -m asm.main %*
pause
