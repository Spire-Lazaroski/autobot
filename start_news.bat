@echo off
REM Start only the news collector (start_lab.bat also starts it), under its supervisor: a crash is
REM logged to logs\news.log and the collector restarted (max 3 per hour). It never trades.
REM If one is already running, the new copy says so and stops.
cd /d %~dp0
start "autobot news" /min cmd /k .venv\Scripts\python run_news.py
echo News collector starting (minimized window "autobot news"). Check the News page in the lab.
