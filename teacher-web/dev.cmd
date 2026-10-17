@echo off
rem 双击启动老师端；TEMP 指到纯英文目录，避免 esbuild 在中文临时目录下报 Access is denied
set TMP=G:\deepseek\.tmp
set TEMP=G:\deepseek\.tmp
cd /d "%~dp0"
call npm.cmd run dev
pause
