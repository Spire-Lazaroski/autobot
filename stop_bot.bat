@echo off
REM Stop the paper bot normally: it closes ALL positions (Stop + flatten) and exits.
REM The supervisor sees a normal stop and does not restart it.
cd /d %~dp0
echo This closes every open position in the paper account and stops the bot.
choice /c YN /m "Stop and flatten now"
if errorlevel 2 exit /b
type nul > STOP
echo STOP file written. The bot flattens and exits within a few seconds.
