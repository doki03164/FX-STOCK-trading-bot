"""一鍵啟動。

    python start.py            外匯 + 美股 + 台股，開啟儀表板
    python start.py fx         只跑外匯
    python start.py us         只跑美股
    python start.py tw         只跑台股
    python start.py --no-open  不自動開瀏覽器

第一次啟動會自動補齊缺少的資料（下載報價、跑回測），之後只要幾秒。
這支程式取代了 start.bat —— 批次檔在 Windows 上對編碼與行尾很敏感，用 Python
當進入點就沒有這個問題。
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
NEED = ["pandas", "numpy", "yfinance", "pyarrow"]


def banner(msg):
    print(f"\n{'=' * 70}\n  {msg}\n{'=' * 70}", flush=True)


def preflight():
    if sys.version_info < (3, 9):
        print(f"需要 Python 3.9 以上，目前是 {sys.version.split()[0]}")
        return False

    missing = []
    for m in NEED:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if missing:
        print(f"缺少套件 {', '.join(missing)}，安裝中…", flush=True)
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", "-r",
                            os.path.join(HERE, "requirements.txt")])
        if r.returncode:
            print("\n安裝失敗。請手動執行：")
            print(f"  {sys.executable} -m pip install -r requirements.txt")
            return False
        print("安裝完成。", flush=True)
    return True


def main():
    banner("fxbot / usbot — 交易操作手冊 策略儀表板")
    if not preflight():
        input("\n按 Enter 關閉…")
        return 1

    sys.path.insert(0, HERE)
    os.chdir(HERE)
    try:
        import app
        return app.main()
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n啟動失敗：{type(e).__name__}: {e}")
        input("按 Enter 關閉…")
        return 1


if __name__ == "__main__":
    sys.exit(main())
