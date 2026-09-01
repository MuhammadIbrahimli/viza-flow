@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python not found in PATH. Install Python 3.10+ and retry.
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv venv
  if errorlevel 1 exit /b 1
)

call "venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt
if errorlevel 1 exit /b 1

if not exist "temp" mkdir "temp"

echo Starting API on http://127.0.0.1:8000 ...
start "VizaFlow API" cmd /k "cd /d "%~dp0" && call venv\Scripts\activate.bat && python run_server.py"

timeout /t 3 /nobreak >nul

set "CF="
where cloudflared >nul 2>&1 && set "CF=cloudflared"
if not defined CF if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if not defined CF if exist "%ProgramFiles%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles%\cloudflared\cloudflared.exe"

if not defined CF (
  echo.
  echo cloudflared is not in PATH.
  echo Install: winget install --id Cloudflare.cloudflared
  echo API is already running at http://127.0.0.1:8000  (GET /ping)
  exit /b 1
)

echo Starting Cloudflare quick tunnel...
echo Look for https://....trycloudflare.com in the log below.
"%CF%" tunnel --url http://127.0.0.1:8000
