@echo off
REM Starts the strategy lab: API (FastAPI) + web (Vite) on 127.0.0.1, then opens the browser.
REM Ports come from .env (API_PORT, WEB_PORT). Close the two windows to stop.
cd /d %~dp0
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
start "autobot API :%API_PORT%" cmd /k .venv\Scripts\python -m api
start "autobot web :%WEB_PORT%" cmd /k "cd web && npm run dev"
REM News collector: backfills what is missing, then collects live news (never trades)
start "autobot news" cmd /k .venv\Scripts\python news_collector.py
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:%WEB_PORT%/
