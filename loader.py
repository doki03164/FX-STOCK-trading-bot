"""Load cached bars and align the two context timeframes onto the execution frame.

The alignment rule is the whole point of this module: an execution bar opening at t
may only see a context bar that had already CLOSED at or before t. Joining on the
context bar's open stamp instead would hand the strategy the rest of that bar.

A bar's close time is taken as the NEXT bar's open, not open+fixed-span, because
months are not a fixed span and FX weeks are not a clean 7 days.
"""
import os
import functools
import numpy as np
import pandas as pd

import config as C
import structure as S


@functools.lru_cache(maxsize=256)
def bars(sym, tf="1h"):
    p = os.path.join(C.DATA, tf, sym.replace("=", "_") + ".csv")
    d = pd.read_csv(p, parse_dates=["date"])
    d["date"] = pd.to_datetime(d.date, utc=True)
    return d.reset_index(drop=True)


def _bar_end(dates):
    """Close time of each bar = the next bar's open; the last one gets the median span."""
    nxt = dates.shift(-1)
    span = dates.diff().median()
    return nxt.fillna(dates.iloc[-1] + (span if pd.notna(span) else pd.Timedelta("1D")))


@functools.lru_cache(maxsize=128)
def frame(sym, tf="1h", swing_n=5, min_swing=2.0, swing_n_htf=3):
    """Everything the gates need for one pair on one execution timeframe.

    Context timeframes use a smaller fractal (`swing_n_htf`) because they carry an
    order of magnitude fewer bars — a 5-bar fractal on the monthly would confirm a
    swing most of a year late.
    """
    ex = bars(sym, tf).copy()
    st = S.build(ex, swing_n, min_swing)

    h, l, c = ex.high.values, ex.low.values, ex.close.values
    ex["atr"] = S.atr(h, l, c, 14)
    ex["ema25"] = S.ema(c, 25)
    ex["ema50"] = S.ema(c, 50)

    left = ex[["date"]].copy()
    ctx = {}
    for tag, ctf in (("mid", C.TF_STACK[tf]["mid"]), ("high", C.TF_STACK[tf]["high"])):
        hd = bars(sym, ctf)
        hst = S.build(hd, swing_n_htf, min_swing)
        hi = pd.DataFrame({"key": _bar_end(hd.date),
                           "trend": hst["trend"], "nlab": hst["nlab"]}).sort_values("key")
        m = pd.merge_asof(left, hi, left_on="date", right_on="key", direction="backward")
        ex[f"{tag}_trend"] = m["trend"].fillna(0).astype(int).values
        ex[f"{tag}_nlab"] = m["nlab"].fillna(0).astype(int).values
        ctx[tag] = (hd, hst)

    return {"d": ex, "st": st, "ctx": ctx, "tf": tf}


def htf_barrier(bundle, tag, t, price, direction, min_gap=0.0):
    """Nearest confirmed context-timeframe swing level in the trade's direction.

    `t` is the execution bar's open time; only pivots whose confirmation bar had
    already closed by then are eligible. `min_gap` skips levels sitting inside the
    trade's own risk unit — a "key level" you would bother measuring to has to be
    further away than your stop, otherwise every micro-swing inside the impulse we
    just broke would count as an obstacle.
    """
    hd, hst = bundle["ctx"][tag]
    if len(hst["piv_i"]) == 0:
        return np.inf * direction
    end = _bar_end(hd.date).values
    conf_time = end[np.minimum(hst["piv_c"], len(hd) - 1)]
    want = 1 if direction > 0 else -1
    m = (conf_time <= np.datetime64(t)) & (hst["piv_k"] == want)
    p = hst["piv_p"][m]
    p = p[(p - price) * direction > min_gap]
    if len(p) == 0:
        return np.inf * direction
    return float(p.min()) if direction > 0 else float(p.max())
