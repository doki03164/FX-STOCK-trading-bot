"""Portfolio layer: turn a stream of accepted setups into one account's equity curve.

The gates decide whether a trade is *valid*; this file decides whether it is
*allowed* — §05 correlation, §10 停止交易的條件, and the two-trades-a-day cap. Those
rules are not decoration. A 1% strategy that quietly runs four correlated pairs is
a 4% strategy, and the drawdown numbers you get without them are fiction.
"""
import numpy as np
import pandas as pd

import config as C


def _ccy_delta(sym, dr):
    """Long EUR/USD = +1 EUR, -1 USD. Netting these is the correlation check."""
    base, quote = C.pair(sym)
    return {base: dr, quote: -dr}


def run(trades, acct=None, capital=None):
    """Sequence accepted candidates through one account.

    `trades` needs entry_time, exit_time, symbol, dr, r_net — i.e. the output of
    gates.scan filtered by gates.preconditions.
    """
    a = dict(C.ACCOUNT)
    if acct:
        a.update(acct)
    eq = float(capital or a["capital"])

    t = trades.dropna(subset=["entry_time", "exit_time"]).sort_values("entry_time")
    t = t.reset_index(drop=True)
    if not len(t):
        return pd.DataFrame(), pd.Series(dtype=float), {"trades": 0, "rejects": {}}

    open_pos = []          # (exit_time, symbol, dr, risk_amt, r_net, row_id)
    exposure = {}          # currency -> net units of risk
    taken, curve = [], [(t.entry_time.iloc[0], eq)]
    day_count, day_pnl, week_pnl, day_start_eq, week_start_eq = {}, {}, {}, {}, {}
    streak, streak_pause_day = 0, None
    peak = eq
    rejects = {}

    def settle(upto):
        nonlocal eq, streak
        while open_pos and open_pos[0][0] <= upto:
            xt, sym, dr, risk_amt, r, rid = open_pos.pop(0)
            pnl = r * risk_amt
            eq += pnl
            d, wk = xt.date(), (xt.isocalendar().year, xt.isocalendar().week)
            day_pnl[d] = day_pnl.get(d, 0.0) + pnl
            week_pnl[wk] = week_pnl.get(wk, 0.0) + pnl
            for k, v in _ccy_delta(sym, dr).items():
                exposure[k] = exposure.get(k, 0) - v
            streak = streak + 1 if pnl < 0 else 0   # 連續 3 筆虧損 → 暫停 1 日
            taken[rid]["exit_equity"] = eq
            taken[rid]["pnl"] = pnl
            curve.append((xt, eq))

    for i, r in t.iterrows():
        et = r.entry_time
        settle(et)

        d = et.date()
        wk = (et.isocalendar().year, et.isocalendar().week)
        day_start_eq.setdefault(d, eq)
        week_start_eq.setdefault(wk, eq)

        # 連續 3 筆虧損 → 暫停 1 日. The counter resets on the pause so the day after
        # starts clean; without the reset a bad streak would freeze the account forever.
        if streak >= a["loss_streak"]:
            streak_pause_day = d
            streak = 0

        why = None
        if streak_pause_day == d:
            why = "loss_streak"
        elif day_pnl.get(d, 0.0) <= -a["daily_stop"] * day_start_eq[d]:
            why = "daily_stop"            # 單日虧損 2%
        elif week_pnl.get(wk, 0.0) <= -a["weekly_stop"] * week_start_eq[wk]:
            why = "weekly_stop"           # 單週虧損 5%
        elif day_count.get(d, 0) >= a["max_trades_day"]:
            why = "day_limit"             # 當日已開 2 筆
        elif len(open_pos) >= a["max_open"]:
            why = "max_open"              # 保證金
        elif any(p[1] == r.symbol for p in open_pos):
            why = "same_pair"
        else:
            for k, v in _ccy_delta(r.symbol, r.dr).items():
                cur = exposure.get(k, 0)
                if abs(cur + v) > a["max_ccy_exposure"] and abs(cur + v) > abs(cur):
                    why = f"corr_{k}"     # §05 相關性檢查
                    break

        if why:
            rejects[why] = rejects.get(why, 0) + 1
            continue

        risk_amt = eq * a["risk_pct"]
        rid = len(taken)
        rec = dict(r)
        rec.update(entry_equity=eq, risk_amt=risk_amt, pnl=np.nan, exit_equity=np.nan)
        taken.append(rec)
        open_pos.append((r.exit_time, r.symbol, r.dr, risk_amt, r.r_net, rid))
        open_pos.sort(key=lambda p: p[0])
        day_count[d] = day_count.get(d, 0) + 1
        for k, v in _ccy_delta(r.symbol, r.dr).items():
            exposure[k] = exposure.get(k, 0) + v

    settle(pd.Timestamp.max)

    tk = pd.DataFrame(taken)
    cv = pd.DataFrame(curve, columns=["t", "equity"]).groupby("t").last()
    cur = cv["equity"].sort_index()
    s = stats(tk, cur)
    s["rejects"] = rejects
    return tk, cur, s


def stats(tk, curve):
    """Account-level performance. Sharpe is computed on DAILY equity, not per trade."""
    if not len(tk) or not len(curve):
        return {"trades": 0}
    eq0, eq1 = float(curve.iloc[0]), float(curve.iloc[-1])
    days = max((curve.index[-1] - curve.index[0]).days, 1)
    years = days / 365.25
    daily = curve.resample("1D").last().ffill()
    ret = daily.pct_change().dropna()
    dd = curve / curve.cummax() - 1.0
    w = tk[tk.pnl > 0]
    lo = tk[tk.pnl <= 0]
    gp, gl = float(w.pnl.sum()), float(-lo.pnl.sum())
    mdd = float(dd.min())
    cagr = (eq1 / eq0) ** (1 / years) - 1 if years > 0 and eq1 > 0 else 0.0
    return {
        "trades": int(len(tk)),
        "total_return": eq1 / eq0 - 1.0,
        "cagr": float(cagr),
        "final_equity": eq1,
        "max_dd": mdd,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else 0.0,
        "sharpe": float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0,
        "win_rate": float((tk.pnl > 0).mean()),
        "profit_factor": float(gp / gl) if gl > 0 else float("inf") if gp > 0 else 0.0,
        "expectancy_r": float(tk.r_net.mean()),
        "avg_win_r": float(w.r_net.mean()) if len(w) else 0.0,
        "avg_loss_r": float(lo.r_net.mean()) if len(lo) else 0.0,
        "best_r": float(tk.r_net.max()),
        "worst_r": float(tk.r_net.min()),
        "trades_per_week": float(len(tk) / max(days / 7, 1)),
        "days": int(days),
    }


# ---------------------------------------------------------------- fast sweep path
def sequence(trades):
    """Non-overlap filter only — one position per pair, in time order.

    The sweep runs this thousands of times, so it skips the account bookkeeping and
    keeps just the constraint that actually changes which trades exist. Equity-path
    effects are re-checked with the full `run()` on the shortlisted configs.
    """
    if not len(trades):
        return trades
    t = trades.sort_values("entry_time")
    ent = t.entry_time.values
    ext = t.exit_time.values
    sym = t.symbol.values
    busy = {}
    keep = np.zeros(len(t), bool)
    for i in range(len(t)):
        s = sym[i]
        if s in busy and ent[i] < busy[s]:
            continue
        busy[s] = ext[i]
        keep[i] = True
    return t[keep]


def r_stats(t):
    """R-space summary used for every sweep cell."""
    n = len(t)
    if n == 0:
        return dict(trades=0, expectancy=0.0, win_rate=0.0, profit_factor=0.0,
                    total_r=0.0, max_dd_r=0.0, tstat=0.0)
    r = t.r_net.values
    cum = np.cumsum(r)
    dd = cum - np.maximum.accumulate(np.r_[0.0, cum])[1:]
    gp = r[r > 0].sum()
    gl = -r[r <= 0].sum()
    return dict(
        trades=int(n), expectancy=float(r.mean()), win_rate=float((r > 0).mean()),
        profit_factor=float(gp / gl) if gl > 0 else float("inf") if gp > 0 else 0.0,
        total_r=float(cum[-1]), max_dd_r=float(dd.min()),
        # t-statistic of the expectancy: with thousands of sweep cells, the winners
        # are partly luck, and this is the cheapest way to see how much of one.
        tstat=float(r.mean() / r.std() * np.sqrt(n)) if r.std() > 0 else 0.0,
    )
