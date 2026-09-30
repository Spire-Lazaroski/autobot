@echo off
REM Turn OFF automatic start at log-on. Running programs are not stopped (use stop_bot.bat).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Action off
pause
