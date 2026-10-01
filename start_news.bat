@echo off
REM Start only the news collector (start_lab.bat also starts it). It fills in missed stories,
REM then collects live news. It never trades. If one is already running, the new copy says so and stops.
cd /d %~dp0
start "autobot news" /min cmd /k .venv\Scripts\python news_collector.py
echo News collector starting (minimized window "autobot news"). Check the News page in the lab.
