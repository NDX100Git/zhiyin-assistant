@echo off
set TMP=G:\deepseek\.tmp
set TEMP=G:\deepseek\.tmp
cd /d "%~dp0"
call npm.cmd run build
pause
