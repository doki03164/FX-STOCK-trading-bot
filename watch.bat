@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
title fxbot 24hr watch
python watch.py %*
echo.
pause >nul
