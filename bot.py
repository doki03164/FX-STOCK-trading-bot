"""Live scanner: run GATE 1–7 on the newest bars and say what to do, and when.

This produces ORDER TICKETS, not orders. It does not connect to a broker and does
not place, modify or cancel anything — retail FX has no order API here, and the
manual's GATE 7 is a human pressing the button after checking A1–E4 anyway. What it
CAN do is remove every judgement call from that moment: the price to enter at, the
price to leave at, the size, the alert to set, and the date the plan expires.

    python bot.py                  # refresh data, then scan all three ladders
    python bot.py --nofetch        # scan the cached bars
    python bot.py --nofetch 1d     # only the daily-execution ladder
"""
import os, sys, json, subprocess
import numpy as np
import pandas as pd

import config as C
import gates
import quotes
import structure as S
from loader import frame, htf_barrier, bars
from report import LADDERS_BY_MARKET

LOT = 100_000.0                       # units in one standard lot
BAR = {"1h": pd.Timedelta("1h"), "4h": pd.Timedelta("4h"), "1d": pd.Timedelta("1D")}


def pip_value_usd(sym, price, quotes):
    """USD value of one pip on one standard lot.

    Quote currency is USD  -> fixed $10.
    Base currency is USD   -> 10 / price.
    Neither                -> convert the quote currency to USD with a live cross.
    """
    base, quote = C.pair(sym)
    per_pip_quote = C.pip_size(sym) * LOT
    if quote == "USD":
        return per_pip_quote
    if base == "USD":
        return per_pip_quote / price
    for cand, inv in ((f"{quote}USD=X", False), (f"USD{quote}=X", True)):
        if cand in quotes:
            r = quotes[cand]
            return per_pip_quote * (1.0 / r if inv else r)
    return per_pip_quote      # last resort: treat the quote ccy as USD-parity


def _entry_rule(mode, dr, near, dec, tf):
    side = "買進" if dr > 0 else "賣出"
    if mode == "limit":
        return (f"在 {near:.{dec}f} 掛{side}限價單，SL/TP 一併設定後離開螢幕。"
                f"價格觸及即成交，不需要盯盤。")
    if mode == "limit_confirm":
        return (f"在 {near:.{dec}f} 掛{side}限價單。成交後檢查該根 {tf} K 棒收線："
                f"若沒有出現順勢拒絕訊號（長影線／吞噬／pin bar），"
                f"就在收盤價平倉認賠小額出場。")
    return (f"等價格進入區域後，等當根 {tf} K 棒<b>收線</b>；"
            f"出現順勢拒絕訊號才以市價{side}。K 棒未收線不進場。")


def scan_live(equity, tf, px_map):
    """Every live plan on one execution timeframe, with entry and exit timing."""
    P = LADDERS_BY_MARKET.get(C.MARKET, LADDERS_BY_MARKET['fx'])[tf]
    out = []
    for sym in C.SYMBOLS:
        try:
            b = frame(sym, tf, 5, P["min_swing"], _market=C.MARKET)
        except FileNotFoundError:
            continue
        d, st = b["d"], b["st"]
        o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
        atr, e25, e50 = d.atr.values, d.ema25.values, d.ema50.values
        dates, N = d.date.values, len(d)
        i = N - 1                                   # newest CLOSED bar
        dr = int(st["trend"][i])
        if dr == 0 or not np.isfinite(atr[i]):
            continue

        # --- GATE 1/2: structure and maturity straight off the state arrays
        nlab, legs = int(st["nlab"][i]), int(st["legs"][i])
        ext = float(st["lastH_p"][i] if dr > 0 else st["lastL_p"][i])
        base = float(st["lastL_p"][i] if dr > 0 else st["lastH_p"][i])
        if not np.isfinite(ext) or not np.isfinite(base) or abs(ext - base) < 1e-9:
            continue
        if int(st["last_kind"][i]) != dr:           # C1: not in a pullback yet
            continue

        # --- GATE 4: rebuild the HPCZ exactly as the backtest does
        w = P["band_atr"] * atr[i]
        fibs = S.fib_levels(base, ext)
        best = None
        for x in fibs + [e25[i], e50[i]]:
            if not np.isfinite(x):
                continue
            lo, hi = x - w, x + w
            r = (ext - x) / (ext - base) if dr > 0 else (x - ext) / (base - ext)
            if not (0.236 <= r <= 0.886):
                continue
            f_ema = (lo <= e25[i] <= hi) or (lo <= e50[i] <= hi)
            f_fib = any(lo <= q <= hi for q in fibs)
            f_sr = S.sr_touches(st, i, lo, hi, 600, 2)
            f_tl = S.trendline(st, i, dr, lo, hi, 3, 0.35, atr[i])
            a1 = np.nanmean(h[i - 5:i + 1] - l[i - 5:i + 1])
            a2 = np.nanmean(h[i - 11:i - 5] - l[i - 11:i - 5])
            f_pat = bool(a2 > 0 and a1 / a2 < 0.8)
            n_conf = 1 + sum((f_ema, f_fib, f_sr, f_tl, f_pat))
            key = (n_conf, -abs(r - 0.5))
            if best is None or key > best[0]:
                best = (key, dict(lo=lo, hi=hi, n=n_conf, ema=f_ema, r=r,
                                  fac=dict(結構位=True, EMA=f_ema, 斐波那契=f_fib,
                                           水平位=f_sr, 趨勢線=f_tl, 延續形態=f_pat)))
        if best is None:
            continue
        z = best[1]

        near = z["hi"] if dr > 0 else z["lo"]       # edge price reaches first
        far = z["lo"] if dr > 0 else z["hi"]
        entry, tp = near, ext
        sl = far - dr * 0.2 * atr[i]
        risk, reward = abs(entry - sl), (tp - entry) * dr
        if risk <= 0 or reward <= 0:
            continue
        rr, sl_atr = reward / risk, risk / atr[i]
        bar_ = [htf_barrier(b, t, dates[i], entry, dr, risk) for t in ("mid", "high")]
        barrier = min(bar_) if dr > 0 else max(bar_)
        room = (barrier - entry) * dr / risk if np.isfinite(barrier) else 99.0

        hi_lab = C.TF_LABEL[C.TF_STACK[tf]["high"]]
        mid_lab = C.TF_LABEL[C.TF_STACK[tf]["mid"]]
        checks = {
            "A1 ≥3 同向標籤": nlab >= 3,
            "A3 趨勢未過成熟": legs <= P["max_legs"],
            f"B1 {hi_lab}階段明確": bool(d.high_trend.values[i] != 0
                                        and d.high_nlab.values[i] >= 2),
            f"B2 {mid_lab}與{C.TF_LABEL[tf]}同向": bool(d.mid_trend.values[i] == dr),
            "B3 關鍵位空間足夠": room >= P["min_rr"],
            f"C2 共振 ≥ {P['min_conf']}": z["n"] >= P["min_conf"],
            "C3 含 25/50 EMA": z["ema"],
            f"E1 RR ≥ {P['min_rr']}": rr >= P["min_rr"],
            f"E2 SL ≥ {P['sl_atr_mult']}×ATR": sl_atr >= P["sl_atr_mult"],
        }
        if P["mtf"] != "daily+4h":
            checks.pop(f"B2 {mid_lab}與{C.TF_LABEL[tf]}同向")
        if P["mtf"] == "off":
            checks.pop(f"B1 {hi_lab}階段明確")
        failed = [k for k, v in checks.items() if not v]

        px = float(c[i])
        if C.is_fx(sym):
            pipv = pip_value_usd(sym, px_map.get(sym, px), px_map)
        else:
            pipv = C.pip_size(sym)          # $0.01 move on one share = $0.01
        risk_usd = equity * C.ACCOUNT["risk_pct"]
        pips = risk / C.pip_size(sym)
        lots = risk_usd / max(pips * pipv, 1e-9)
        dec = (3 if C.pair(sym)[1] in ("JPY", "HUF") else 5) if C.is_fx(sym) else 2
        last_close = pd.Timestamp(dates[i]) + BAR[tf]

        # state comes from quotes.restate so the freshly-scanned book and the live
        # 10-second refresh can never disagree about what is actionable
        stub = {"sym": sym, "dr": int(dr), "zone_lo": float(z["lo"]),
                "zone_hi": float(z["hi"]), "entry": float(entry), "sl": float(sl),
                "invalidate": float(far - dr * 0.5 * atr[i]), "failed": failed}
        lv = quotes.restate(stub, px)
        out.append({
            "sym": sym, "name": C.UNIVERSE[sym], "tf": tf, "dr": int(dr),
            "state": lv["state"], "why": lv["why"], "in_zone": lv["in_zone"],
            "price": round(px, dec), "as_of": str(last_close)[:16],
            "next_bar": str(last_close + BAR[tf])[:16],
            "expires": str(last_close + BAR[tf] * gates.PLAN_TTL)[:10],
            "max_hold_until": str(last_close + BAR[tf] * gates.MAX_HOLD)[:10],
            "zone_lo": round(float(z["lo"]), dec), "zone_hi": round(float(z["hi"]), dec),
            "entry": round(float(entry), dec), "sl": round(float(sl), dec),
            "tp": round(float(tp), dec),
            "invalidate": round(float(far - dr * 0.5 * atr[i]), dec),
            "gap_pips": lv["gap_pips"], "gap_pct": lv["gap_pct"],
            "rr": round(float(rr), 2), "sl_atr": round(float(sl_atr), 2),
            "n_conf": int(z["n"]),
            "factors": [k for k, v in z["fac"].items() if v],
            "legs": int(legs), "nlab": int(nlab), "retrace": round(float(z["r"]), 3),
            "unit": "pips" if C.is_fx(sym) else "美元/股",
            "size_label": "手數" if C.is_fx(sym) else "股數",
            "risk_pips": round(pips, 1), "reward_pips": round(reward / (C.pip_size(sym) if C.is_fx(sym) else 1.0), 2),
            "lots": round(float(lots), 2), "risk_usd": round(float(risk_usd), 2),
            "reward_usd": round(float(risk_usd * rr), 2),
            "checks": {k: bool(v) for k, v in checks.items()}, "failed": failed,
            "entry_rule": _entry_rule(P["entry_mode"], dr, float(entry), dec,
                                      C.TF_LABEL[tf]),
        })
    return out


def main():
    if "--nofetch" not in sys.argv:
        print("refreshing bars...")
        subprocess.run([sys.executable, os.path.join(C.HERE, "fetch.py")], check=False)

    C.set_market(sys.argv[sys.argv.index("--market") + 1]
                 if "--market" in sys.argv else "fx")
    equity = C.ACCOUNT["capital"]
    stp = os.path.join(C.HERE, "state.json")
    if os.path.exists(stp):
        equity = json.load(open(stp)).get("equity", equity)

    # not every name has an intraday file (US intraday is capped at 730 days and a
    # few listings are younger), so fall back to the daily close for the quote
    px_map = {}
    for s in C.SYMBOLS:
        for tf in ("1h", "1d"):
            try:
                px_map[s] = float(bars(s, tf, C.MARKET).close.iloc[-1])
                break
            except FileNotFoundError:
                continue
    _L = LADDERS_BY_MARKET.get(C.MARKET, LADDERS_BY_MARKET['fx'])
    tfs = [a for a in sys.argv[1:] if a in _L] or list(_L)
    plans = []
    for tf in tfs:
        plans += scan_live(equity, tf, px_map)
    order = {"ready": 0, "armed": 1, "stale": 2, "blocked": 3,
             "passed": 4, "stopped": 5, "invalid": 6}
    plans.sort(key=lambda p: (order.get(p["state"], 9), len(p["failed"]), -p["rr"]))

    ready = [p for p in plans if p["state"] == "ready"]
    armed = [p for p in plans if p["state"] == "armed"]
    print(f"\n{'='*86}\n交易操作手冊 — 即時掃描   帳戶 ${equity:,.0f}   風險/筆 "
          f"{C.ACCOUNT['risk_pct']*100:.0f}%\n{'='*86}")
    for tf in tfs:
        s = C.TF_STACK[tf]
        n = [p for p in plans if p["tf"] == tf]
        print(f"  {C.TF_LABEL[tf]}執行（方向看{C.TF_LABEL[s['high']]}、"
              f"空間看{C.TF_LABEL[s['mid']]}）→ {len(n)} 個計畫，"
              f"{sum(1 for p in n if p['state'] == 'ready')} 個可執行")
    _unit = "組貨幣對" if C.MARKET == "fx" else "檔股票"
    print(f"共 {len(C.SYMBOLS)} {_unit} × {len(tfs)} 個週期"
          f"  ·  可執行 {len(ready)}  等待進場 {len(armed)}\n")

    for p in (ready + armed)[:12]:
        tag = "✓ 現在可下單" if p["state"] == "ready" else "· 等待價格到區"
        print(f"[{p['tf']:>3s}] {p['name']:9s} {'多' if p['dr'] > 0 else '空'}  {tag}")
        sz = f"手數 {p['lots']:.2f}" if C.is_fx(p["sym"]) else f"股數 {p['lots']:.0f}"
        print(f"       進場 {p['entry']}  SL {p['sl']}  TP {p['tp']}  "
              f"RR {p['rr']:.2f}  {sz}  風險 ${p['risk_usd']:,.0f}")
        print(f"       {p['entry_rule']}")
        u = "pips" if C.is_fx(p["sym"]) else "美元/股"
        print(f"       出場：TP {p['tp']}（+{p['reward_pips']} {u}）／"
              f"SL {p['sl']}（−{p['risk_pips']} {u}）／"
              f"收線越過 {p['invalidate']} 即作廢／計畫 {p['expires']} 到期")
        if p["state"] == "armed":
            print(f"       距進場區 {abs(p['gap_pips'])} pips（{p['gap_pct']}%）"
                  f"，設價格提醒於 {p['entry']}")

    if not ready:
        print("沒有可立即執行的交易。手冊第 00 節：這是正常的，"
              "不要為了有事做而放寬條件。")
    elif len(ready) > C.ACCOUNT["max_trades_day"]:
        print(f"注意：可執行 {len(ready)} 筆，但當日上限為 "
              f"{C.ACCOUNT['max_trades_day']} 筆。挑 RR 最高的執行。")

    out = {"as_of": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
           "equity": equity, "scanned": len(C.SYMBOLS) * len(tfs),
           "tfs": tfs, "params": {t: LADDERS_BY_MARKET.get(C.MARKET, LADDERS_BY_MARKET["fx"])[t]
                     for t in tfs}, "plans": plans,
           "account": C.ACCOUNT}
    path = C.art("plan","json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwrote {path}")
    print("提醒：本程式只產生訂單參數，不會下單。GATE 7 的執行由你完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
