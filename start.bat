@echo off
REM ============================================================
REM  fxbot - 一鍵啟動
REM  雙擊本檔即可：自動補齊缺少的資料、啟動儀表板、開啟瀏覽器。
REM ============================================================
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
title fxbot - 交易操作手冊 儀表板

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   找不到 python。請先安裝 Python 3.10 以上並勾選 "Add to PATH"。
  echo   https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

python -c "import pandas, numpy, yfinance, pyarrow" >nul 2>nul
if errorlevel 1 (
  echo   安裝相依套件中，請稍候...
  python -m pip install --quiet --disable-pip-version-check -r requirements.txt
)

python app.py %*

echo.
echo   已停止。按任意鍵關閉視窗。
pause >nul
