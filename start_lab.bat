@echo off
REM Starts the strategy lab: API (FastAPI) + web (Vite) on 127.0.0.1, then opens the browser.
REM Ports come from .env (API_PORT, WEB_PORT). Close the windows to stop.
REM "start_lab.bat auto" (used by the log-on task): minimized windows, no browser.
cd /d %~dp0
REM Temporary files on this drive (the system drive C: is nearly full): tmp\autobot at the drive root
set TEMP=%~d0\tmp\autobot
set TMP=%~d0\tmp\autobot
if not exist "%TEMP%" mkdir "%TEMP%"
set MIN=
if /i "%~1"=="auto" set MIN=/min
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
)
.venv\Scripts\pip install -q -r requirements.txt
if not exist .env copy .env.example .env >nul
set API_PORT=8710
set WEB_PORT=5190
for /f "usebackq tokens=1,2 delims==" %%a in (".env") do (
  if "%%a"=="API_PORT" set API_PORT=%%b
  if "%%a"=="WEB_PORT" set WEB_PORT=%%b
)
if not exist web\node_modules (
  pushd web
  call npm install
  popd
)
start "autobot API :%API_PORT%" %MIN% cmd /k .venv\Scripts\python -m api
start "autobot web :%WEB_PORT%" %MIN% cmd /k "cd web && npm run dev"
REM News collector under its supervisor (restarts it after a crash, max 3 per hour): backfills
REM what is missing, then collects live news (never trades)
start "autobot news" %MIN% cmd /k .venv\Scripts\python run_news.py
if /i "%~1"=="auto" exit /b
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:%WEB_PORT%/
