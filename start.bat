@echo off
REM First run: creates a virtual environment and installs everything. Then opens the dashboard.
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
if not exist .env (
  copy .env.example .env
  echo Paste your Alpaca PAPER keys into .env, save, then run start.bat again.
  notepad .env
  exit /b
)
.venv\Scripts\streamlit run app.py
