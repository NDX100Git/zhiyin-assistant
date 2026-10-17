@echo off
rem 双击启动老师端；TEMP 指到项目内 .runtime 目录，避免 esbuild 在中文临时目录下报 Access is denied
set "TMP=%~dp0..\.runtime\tmp"
set "TEMP=%TMP%"
if not exist "%TMP%" mkdir "%TMP%"
cd /d "%~dp0"
call npm.cmd run dev
pause
