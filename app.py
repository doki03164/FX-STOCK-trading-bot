"""One-click launcher: sets itself up, serves the dashboard, keeps it fresh.

    python app.py            # or just double-click start.bat

First run does whatever is missing — download bars, run the sweep — then opens
http://localhost:8765 in the browser. After that the page is a real app rather than
a saved file: the 重新掃描 button re-runs the scan on the server and reloads, and
the watcher ticks in a background thread on every 1H close.

Everything still works if you just open dashboard.html directly; the buttons simply
hide themselves, because a file:// page has no server to talk to.
"""
import os, sys, json, time, threading, subprocess, webbrowser
import http.server, socketserver, urllib.parse

import config as C

HERE = C.HERE
PORT = int(os.environ.get("FXBOT_PORT", 8765))
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

STATE = {"busy": False, "step": "", "log": [], "last": None}
LOCK = threading.Lock()


def say(msg):
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOCK:
        STATE["log"] = (STATE["log"] + [line])[-60:]


MARKETS = ["fx", "us"]


def run(script, *args, quiet=True):
    say(f"→ {script} {' '.join(args)}".rstrip())
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       capture_output=quiet, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    if r.returncode:
        say(f"! {script} 失敗 (exit {r.returncode})")
        if quiet and r.stdout:
            say(r.stdout.strip().splitlines()[-1][:160])
    return r.returncode == 0


def have_data(market="fx"):
    d = os.path.join(C.HERE, "data", market, "1d")
    return os.path.isdir(d) and len(os.listdir(d)) > 20


def setup(markets):
    """Do only what is missing, so a re-launch costs seconds rather than minutes."""
    for m in markets:
        if not have_data(m):
            say(f"首次啟動：下載 {m} 歷史報價（約 10 分鐘，只做一次）…")
            run("fetch_us.py" if m == "us" else "fetch.py", quiet=False)
        if not os.path.exists(os.path.join(HERE, f"sweep_{m}.csv")):
            say(f"首次啟動：執行 {m} 參數掃描（約 15 分鐘，只做一次）…")
            run("sweep.py", "--market", m, quiet=False)
        if not os.path.exists(os.path.join(HERE, f"plan_{m}.json")):
            run("bot.py", "--nofetch", "--market", m)
        if not os.path.exists(os.path.join(HERE, f"dashboard_{m}.html")):
            run("report.py", "--market", m)


def scan(fetch=True):
    with LOCK:
        if STATE["busy"]:
            return False
        STATE["busy"], STATE["step"] = True, "抓取報價"
    try:
        for m in MARKETS:
            with LOCK:
                STATE["step"] = f"{m} 抓取報價"
            if fetch:
                run("fetch.py", "--update", "--market", m)
            with LOCK:
                STATE["step"] = f"{m} 掃描 GATE 1–7"
            run("bot.py", "--nofetch", "--market", m)
            with LOCK:
                STATE["step"] = f"{m} 重建儀表板"
            run("report.py", "--market", m)
        with LOCK:
            STATE["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
        say("完成")
    finally:
        with LOCK:
            STATE["busy"], STATE["step"] = False, ""
    return True


def watcher():
    """Background 1H-close loop. Same schedule as watch.py, in-process."""
    import watch
    while True:
        try:
            w = watch.next_wake()
            time.sleep(max((w - __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc)).total_seconds(), 30))
            if watch.market_open():
                say("K 棒收線，自動重新掃描")
                scan(fetch=True)
            else:
                say("外匯休市，略過")
        except Exception as e:
            say(f"! 監控例外 {type(e).__name__}: {str(e)[:90]}")
            time.sleep(60)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def log_message(self, *a):
        pass                                   # the console is for our own messages

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html", "/fx"):
            self.path = "/dashboard_fx.html"
        elif p in ("/us", "/us.html"):
            self.path = "/dashboard_us.html"
        elif p == "/api/status":
            with LOCK:
                s = {k: STATE[k] for k in ("busy", "step", "last")}
            s["log"] = STATE["log"][-12:]
            return self._json(s)
        elif p == "/api/scan":                 # GET so a plain link works too
            ok = threading.Thread(target=scan, daemon=True).start() or True
            return self._json({"started": ok})
        return super().do_GET()

    def end_headers(self):
        if self.path.endswith(".html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    print("=" * 70)
    print("  fxbot — 交易操作手冊 策略儀表板")
    print("=" * 70)
    want = [m for m in MARKETS if m in sys.argv] or MARKETS
    MARKETS[:] = want
    setup(want)

    if "--no-watch" not in sys.argv:
        threading.Thread(target=watcher, daemon=True).start()
        say("背景監控已啟動（每根 1H K 棒收線後自動重掃）")

    socketserver.TCPServer.allow_reuse_address = True
    for port in range(PORT, PORT + 10):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        print(f"! 連 {PORT}–{PORT+9} 都被佔用，請設定環境變數 FXBOT_PORT")
        return 1

    url = f"http://localhost:{port}/"
    say(f"儀表板 → {url}")
    print("\n  關閉這個視窗就會停止。Ctrl-C 也可以。\n")
    if "--no-open" not in sys.argv:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
