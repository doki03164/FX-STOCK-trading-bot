"""Market structure: swing pivots, HH/HL/LH/LL labels, trend state and maturity.

The one rule this file exists to enforce: a swing point taken with `n` bars either
side is NOT knowable until `n` bars after it printed. Every derived series here is
built by replaying pivots in order of their CONFIRMATION bar, so a value read at
bar i only ever reflects structure a trader could actually have drawn at bar i.
Get this wrong and the whole backtest becomes a very convincing lie.
"""
import numpy as np
import pandas as pd

UP, DOWN, NONE = 1, -1, 0


# ---------------------------------------------------------------- indicators
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def atr(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values


# ---------------------------------------------------------------- pivots
def raw_pivots(h, l, n):
    """Fractal highs/lows: strictly beyond the n bars left, at-least-equal the n right."""
    N = len(h)
    ph = np.ones(N, bool)
    pl = np.ones(N, bool)
    for k in range(1, n + 1):
        lh = np.r_[np.full(k, -np.inf), h[:-k]]
        rh = np.r_[h[k:], np.full(k, -np.inf)]
        ph &= (h > lh) & (h >= rh)
        ll = np.r_[np.full(k, np.inf), l[:-k]]
        rl = np.r_[l[k:], np.full(k, np.inf)]
        pl &= (l < ll) & (l <= rl)
    ph[:n] = ph[-n:] = False
    pl[:n] = pl[-n:] = False
    # a bar can't be both; the wider range wins
    both = ph & pl
    ph[both] = False
    pl[both] = False
    return ph, pl


def _label(seq, k):
    """Label seq[k] against the previous pivot of the same kind."""
    kind, price = seq[k][1], seq[k][2]
    for j in range(k - 1, -1, -1):
        if seq[j][1] == kind:
            if kind == "H":
                return "HH" if price > seq[j][2] else "LH"
            return "HL" if price > seq[j][2] else "LL"
    return "??"


def _run(seq):
    """(direction, label count, impulse legs) for the trailing same-direction run."""
    up_set, dn_set = {"HH", "HL"}, {"LH", "LL"}
    for direction, wanted, impulse in ((UP, up_set, "HH"), (DOWN, dn_set, "LL")):
        k = len(seq) - 1
        while k >= 0 and seq[k][3] in wanted:
            k -= 1
        n = len(seq) - 1 - k
        if n >= 2:  # a run of one label is not a structure
            legs = sum(1 for j in range(k + 1, len(seq)) if seq[j][3] == impulse)
            return direction, n, max(legs, 1)
    return NONE, 0, 0


def build(df, n=5, min_swing=2.0):
    """Per-bar structure state, plus the confirmed-pivot table for later lookups.

    `min_swing` is the reversal threshold in ATR: a fractal only becomes a zigzag
    pivot if it is that far from the previous one. Without it a 5-bar fractal chops
    a genuine impulse into a dozen micro-legs, and the manual's whole geometry —
    a stop 2×ATR away AND a target 2R beyond it — stops being reachable. It is the
    machine equivalent of "縮放到能看見約 150–200 根 K 棒".

    Returns a dict of numpy arrays all indexed by bar. `*_i` fields are bar indices,
    `*_p` fields are prices. Everything is forward-filled from the confirmation bar.
    """
    h, l, c = df.high.values, df.low.values, df.close.values
    N = len(h)
    av = atr(h, l, c, 14)
    ph, pl = raw_pivots(h, l, n)

    # candidate pivots ordered by the bar on which they become knowable
    cand = [(i, "H", h[i]) for i in np.flatnonzero(ph)] + \
           [(i, "L", l[i]) for i in np.flatnonzero(pl)]
    cand.sort(key=lambda t: (t[0] + n, t[0]))

    out = {k: np.full(N, np.nan) for k in
           ("lastH_p", "lastL_p", "prevH_p", "prevL_p", "runstart_p")}
    for k in ("trend", "nlab", "legs", "lastH_i", "lastL_i", "last_kind", "runstart_i"):
        out[k] = np.zeros(N, np.int32)
    out["lastH_i"][:] = -1
    out["lastL_i"][:] = -1
    out["runstart_i"][:] = -1

    seq = []           # (bar_idx, kind, price, label)
    piv_i, piv_k, piv_p, piv_c = [], [], [], []
    state = None
    cur = 0            # next bar to fill

    def snapshot():
        d, nl, legs = _run(seq)
        hi = [s for s in seq if s[1] == "H"]
        lo = [s for s in seq if s[1] == "L"]
        rs_i, rs_p = -1, np.nan
        if d != NONE:
            up_set, dn_set = {"HH", "HL"}, {"LH", "LL"}
            wanted = up_set if d == UP else dn_set
            k = len(seq) - 1
            while k >= 0 and seq[k][3] in wanted:
                k -= 1
            rs_i, rs_p = seq[k + 1][0], seq[k + 1][2]
        return {
            "trend": d, "nlab": nl, "legs": legs,
            "lastH_i": hi[-1][0] if hi else -1, "lastH_p": hi[-1][2] if hi else np.nan,
            "lastL_i": lo[-1][0] if lo else -1, "lastL_p": lo[-1][2] if lo else np.nan,
            "prevH_p": hi[-2][2] if len(hi) > 1 else np.nan,
            "prevL_p": lo[-2][2] if len(lo) > 1 else np.nan,
            "last_kind": 1 if seq[-1][1] == "H" else -1,
            "runstart_i": rs_i, "runstart_p": rs_p,
        }

    def fill(upto, st):
        if st is None or upto <= cur:
            return
        sl = slice(cur, upto)
        for k, v in st.items():
            out[k][sl] = v

    for idx, kind, price in cand:
        conf = idx + n
        if conf >= N:
            break
        fill(conf, state)
        cur = max(cur, conf)

        if seq and seq[-1][1] == kind:
            # same kind twice: the zigzag extends to the more extreme point
            better = price > seq[-1][2] if kind == "H" else price < seq[-1][2]
            if not better:
                continue
            seq[-1] = (idx, kind, price, "")
            seq[-1] = (idx, kind, price, _label(seq, len(seq) - 1))
        else:
            a = av[idx]
            if seq and min_swing > 0 and np.isfinite(a) and \
                    abs(price - seq[-1][2]) < min_swing * a:
                continue                      # too shallow to be a swing — it is noise
            seq.append((idx, kind, price, ""))
            seq[-1] = (idx, kind, price, _label(seq, len(seq) - 1))
        piv_i.append(idx)
        piv_k.append(1 if kind == "H" else -1)
        piv_p.append(price)
        piv_c.append(conf)
        state = snapshot()

    fill(N, state)

    out["piv_i"] = np.array(piv_i, np.int32)
    out["piv_k"] = np.array(piv_k, np.int8)
    out["piv_p"] = np.array(piv_p, float)
    out["piv_c"] = np.array(piv_c, np.int32)
    return out


# ---------------------------------------------------------------- confluence helpers
def sr_touches(st, i, lo, hi, lookback=600, min_touches=2):
    """How many past confirmed pivots reacted inside [lo, hi] — the horizontal S/R test."""
    m = (st["piv_c"] <= i) & (st["piv_i"] >= i - lookback)
    p = st["piv_p"][m]
    return int(((p >= lo) & (p <= hi)).sum()) >= min_touches


def trendline(st, i, direction, lo, hi, min_touches=3, tol=0.35, atr_v=1.0):
    """Project a line through the last `min_touches` same-side pivots to bar i.

    Only counts if the pivots are genuinely collinear (each within `tol` ATR of the
    fit) — the manual is explicit that an eyeballed 2-touch line does not count.
    """
    want = -1 if direction == UP else 1     # uptrend rides its LOWS
    m = (st["piv_c"] <= i) & (st["piv_k"] == want)
    xs, ys = st["piv_i"][m], st["piv_p"][m]
    if len(xs) < min_touches:
        return False
    xs, ys = xs[-min_touches:], ys[-min_touches:]
    if direction == UP and not np.all(np.diff(ys) > 0):
        return False
    if direction == DOWN and not np.all(np.diff(ys) < 0):
        return False
    a, b = np.polyfit(xs, ys, 1)
    if np.max(np.abs(ys - (a * xs + b))) > tol * atr_v:
        return False
    v = a * i + b
    return lo <= v <= hi


def fib_levels(a, b):
    """Retracement prices for the 38.2 / 50 / 61.8 of the impulse a -> b."""
    return [b - (b - a) * r for r in (0.382, 0.5, 0.618)]
