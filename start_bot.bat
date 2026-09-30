@echo off
REM Start the paper bot under its supervisor (restarts it after a crash, max 3 per hour).
REM If a bot is already running, the new copy exits quietly. Paper trading only.
cd /d %~dp0
start "Spire autobot" /min .venv\Scripts\python.exe run_bot.py
echo Bot starting (minimized window "Spire autobot"). Check the Live page in the lab.
