@echo off
REM Double-click to start the 24-hour watcher in its own window.
REM Chinese output needs UTF-8; the default Windows console codepage is cp950.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
title fxbot - 24hr watch
python watch.py %*
echo.
echo 監控已結束。按任意鍵關閉視窗。
pause >nul
