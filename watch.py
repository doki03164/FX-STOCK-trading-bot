"""24-hour watcher: re-scan on every 1H bar close, and log what changed.

On a 1H strategy each closed bar is a decision point, so this parks on the hour
boundary rather than polling blindly: it wakes a little after :00, pulls only the
new bars, re-runs GATE 1–7 on all three ladders, and rebuilds the dashboard.

What it prints is the DIFF, not the whole book. A plan that was waiting yesterday
and is still waiting is not news; a plan that just became executable is. Those
transitions are appended to `events.jsonl` and shown in the live tab, so you can
walk away and read the feed later instead of watching a screen — which is exactly
what the manual asks for (價格提醒, not 盯盤).

    python watch.py                # every 1H close, all three ladders
    python watch.py --every 15     # every 15 minutes instead
    python watch.py --once         # single pass, then exit
    python watch.py --no-report    # skip the dashboard rebuild (faster cycles)

Ctrl-C stops it. Nothing here places orders.
"""
import os, sys, json, time, subprocess, datetime as dt

import config as C

HERE = C.HERE
EVENTS = os.path.join(HERE, "events.jsonl")
PLAN = os.path.join(HERE, "plan.json")
LAG = 90            # seconds after the bar close before Yahoo has the new bar

# Transitions worth interrupting someone for, in priority order.
KIND = {
    "ready":   ("✓ 可下單", "\a"),      # bell only for the one that needs a decision
    "armed":   ("· 新計畫（等待到區）", ""),
    "gone":    ("× 計畫消失", ""),
    "invalid": ("× 區域被貫穿，作廢", ""),
    "lost":    ("× 條件不再成立", ""),
}


def market_open(now=None):
    """FX runs Sunday ~21:00 UTC to Friday ~21:00 UTC."""
    n = now or dt.datetime.now(dt.timezone.utc)
    wd, hr = n.weekday(), n.hour            # Mon=0 … Sat=5, Sun=6
    if wd == 5:
        return False
    if wd == 4 and hr >= 21:
        return False
    if wd == 6 and hr < 21:
        return False
    return True


def next_wake(every_min=None):
    """Next 1H boundary + LAG, or the next fixed interval."""
    n = dt.datetime.now(dt.timezone.utc)
    if every_min:
        step = dt.timedelta(minutes=every_min)
        base = n.replace(second=0, microsecond=0)
        return base + step - dt.timedelta(minutes=base.minute % every_min)
    nxt = (n.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1))
    return nxt + dt.timedelta(seconds=LAG)


def run(args, quiet=True):
    # the children print Chinese; on Windows the default console codepage is cp950
    # and capturing their output would blow up decoding it
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, os.path.join(HERE, args[0])] + args[1:],
                       capture_output=quiet, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return r.returncode


def snapshot():
    if not os.path.exists(PLAN):
        return {}, None
    p = json.load(open(PLAN, encoding="utf-8"))
    return {f"{x['sym']}|{x['tf']}": x for x in p["plans"]}, p


def diff(old, new):
    """Only transitions that change what a human would do."""
    ev = []
    for k, n in new.items():
        o = old.get(k)
        was, now = (o or {}).get("state"), n["state"]
        if was == now:
            continue
        if now == "ready":
            ev.append(("ready", n))
        elif now == "armed" and was not in ("ready",):
            ev.append(("armed", n))
        elif was in ("ready", "armed") and now == "invalid":
            ev.append(("invalid", n))
        elif was in ("ready", "armed") and now == "blocked":
            ev.append(("lost", n))
    for k, o in old.items():
        if k not in new and o.get("state") in ("ready", "armed"):
            ev.append(("gone", o))
    order = list(KIND)
    ev.sort(key=lambda e: order.index(e[0]))
    return ev


KEEP = 2000        # a year of hourly cycles produces a lot of lines


def log(ev, stamp):
    with open(EVENTS, "a", encoding="utf-8") as f:
        for kind, p in ev:
            f.write(json.dumps({
                "t": stamp, "kind": kind, "sym": p["sym"], "name": p["name"],
                "tf": p["tf"], "dr": p["dr"], "entry": p["entry"], "sl": p["sl"],
                "tp": p["tp"], "rr": p["rr"], "lots": p["lots"],
                "state": p["state"],
            }, ensure_ascii=False) + "\n")
    lines = open(EVENTS, encoding="utf-8").readlines()
    if len(lines) > KEEP * 1.5:
        open(EVENTS, "w", encoding="utf-8").writelines(lines[-KEEP:])


def cycle(report=True):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    old, _ = snapshot()

    if run(["fetch.py", "--update"]):
        print(f"[{stamp}] ! 抓取失敗，沿用既有資料")
    if run(["bot.py", "--nofetch"]):
        print(f"[{stamp}] ! 掃描失敗")
        return
    new, p = snapshot()

    ev = diff(old, new)
    if ev:
        log(ev, stamp)
    if report:
        run(["report.py"])

    n = lambda s: sum(1 for x in new.values() if x["state"] == s)
    print(f"[{stamp}] 掃描 {len(new)} 個計畫 · 可下單 {n('ready')} · "
          f"等待 {n('armed')} · 變化 {len(ev)}")
    for kind, q in ev:
        lab, bell = KIND[kind]
        print(f"{bell}    {lab}  [{q['tf']}] {q['name']} "
              f"{'多' if q['dr'] > 0 else '空'}  進場 {q['entry']}  "
              f"SL {q['sl']}  TP {q['tp']}  RR {q['rr']}  手數 {q['lots']}")
        if kind == "ready":
            print(f"        {q['entry_rule']}")


def main():
    a = sys.argv[1:]
    every = None
    if "--every" in a:
        every = int(a[a.index("--every") + 1])
    report = "--no-report" not in a

    print(f"{'='*74}\n交易操作手冊 — 24 小時監控   {len(C.SYMBOLS)} 組貨幣對 × "
          f"{len(C.EXEC_TFS)} 個週期\n{'='*74}")
    print("每根 1H K 棒收線後重新掃描；只印出「有變化」的計畫。Ctrl-C 結束。")
    print("提醒：本程式不會下單，只產生訂單參數。\n")

    if "--once" in a:
        cycle(report)
        return 0

    while True:
        try:
            if market_open():
                cycle(report)
            else:
                print(f"[{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M UTC}] "
                      f"外匯休市，等待開盤")
            w = next_wake(every)
            secs = max((w - dt.datetime.now(dt.timezone.utc)).total_seconds(), 5)
            print(f"    下次掃描 {w:%H:%M:%S} UTC（{secs/60:.0f} 分鐘後）\n")
            time.sleep(secs)
        except KeyboardInterrupt:
            print("\n監控結束。")
            return 0
        except Exception as e:                  # a bad cycle must not kill the watch
            print(f"    ! {type(e).__name__}: {str(e)[:120]} — 60 秒後重試")
            time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
