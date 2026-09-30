@echo off
REM Turn ON automatic start at log-on (2 minutes after you log in): the bot and the lab.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Action on
pause
