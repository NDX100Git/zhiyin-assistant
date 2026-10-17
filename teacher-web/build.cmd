@echo off
set "TMP=%~dp0..\.runtime\tmp"
set "TEMP=%TMP%"
if not exist "%TMP%" mkdir "%TMP%"
cd /d "%~dp0"
call npm.cmd run build
pause
