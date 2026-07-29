"""Parameter sweep: every gate threshold the manual says you must verify yourself.

Two nested layers:
  STRUCTURAL — changes which trades exist at all (zigzag scale, zone width, how you
               enter, where the stop goes). Needs a fresh scan of all 28 pairs.
  FILTER     — changes only which of those trades you accept. Pure boolean mask, so
               a few thousand of them cost almost nothing once the scan is cached.

Every cell is scored twice, on an in-sample and a held-out out-of-sample period.
With this many cells some will look brilliant by chance; the OOS column and the
t-statistic are there so you can tell which.

    python sweep.py            # full run  -> sweep.csv, candidates.parquet
    python sweep.py --quick    # smaller grid for a smoke test
"""
import os, sys, time, itertools, json
import numpy as np
import pandas as pd

import config as C
import gates
import backtest as B

HERE = C.HERE
IS_FRAC = 0.65      # first 65% of each timeframe's own history is in-sample

# `tf` leads the structural grid because it is the axis that actually moved the
# needle: dealing cost is a fixed number of pips, so it eats ~0.08R of a 1H trade
# and ~0.01R of a daily one, while the geometry (leg length in ATR) barely changes.
STRUCTURAL = {
    "tf": ["1h", "4h", "1d"],
    "min_swing": [2.0, 3.0],
    "band_atr": [0.75, 1.0],
    "entry_mode": ["market", "limit", "limit_confirm"],
    "sl_mode": ["wick", "zone"],
}

FILTERS = {
    # E1 and E2 get the finest resolution: they are the two thresholds the manual is
    # most specific about (1:2 and 2×ATR) and the two the heatmap most needs to
    # resolve, because they trade off against each other.
    "min_conf": [3, 4, 5],
    "min_rr": [0.5, 1.0, 1.5, 2.0, 2.5],
    "sl_atr_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
    "max_legs": [1, 2, 3, 99],
    "mtf": ["off", "daily", "daily+4h"],
    "confirm": ["any", "wick", "pin", "engulf"],
}

QUICK = {"tf": ["1d"], "min_swing": [3.0], "band_atr": [0.75],
         "entry_mode": ["limit_confirm"], "sl_mode": ["zone"]}


def split_at(e, frac=IS_FRAC):
    """In/out-of-sample boundary from the data's own span.

    A fixed calendar date cannot work once the timeframes have different histories —
    1H reaches back 2.8 years and daily nearly 30. Each execution timeframe gets the
    same 65/35 proportional cut of its own window instead.
    """
    if not len(e):
        return pd.Timestamp("2100-01-01")
    return pd.Timestamp(pd.Series(e.entry_time.dropna()).quantile(frac))


def _mask(e, f):
    """The manual's A1–E4, but with every threshold supplied by the sweep."""
    m = (e.n_conf >= f["min_conf"]) & (e.rr >= f["min_rr"]) & \
        (e.sl_atr >= f["sl_atr_mult"]) & (e.legs <= f["max_legs"]) & \
        (e.nlab >= 3) & (e.room_R >= f["min_rr"]) & e.has_ema & (e.retrace >= 0.236)
    if f["mtf"] != "off":
        m &= e.d_ok
    if f["mtf"] == "daily+4h":
        m &= e.h4_align
    if f["confirm"] == "wick":
        m &= e.c_wick
    elif f["confirm"] == "pin":
        m &= e.c_pin
    elif f["confirm"] == "engulf":
        m &= e.c_engulf
    return m


def _grid(d):
    keys = list(d)
    for vals in itertools.product(*(d[k] for k in keys)):
        yield dict(zip(keys, vals))


def main(quick=False):
    struct = dict(STRUCTURAL)
    if quick:
        struct.update(QUICK)

    scfg = [s for s in _grid(struct)
            # a resting limit order cannot be conditioned on the shape of the bar that
            # fills it, so only the two confirmation-aware modes vary sl_mode/confirm
            if not (s["entry_mode"] == "limit" and s["sl_mode"] == "wick")]
    fcfg = list(_grid(FILTERS))
    print(f"{len(scfg)} structural x up to {len(fcfg)} filter = "
          f"{len(scfg) * len(fcfg)} runs\n")

    rows, keep_cands = [], []
    t0 = time.time()
    for si, s in enumerate(scfg, 1):
        cand = gates.scan_all(C.SYMBOLS, tf=s["tf"], min_swing=s["min_swing"],
                              band_atr=s["band_atr"], entry_mode=s["entry_mode"],
                              sl_mode=s["sl_mode"])
        e = cand[cand.stage == gates.STAGE["entered"]].copy()
        keep_cands.append(cand)
        split = split_at(e)
        # trade COUNT is not comparable across timeframes — 1H has 2.8 years of history
        # and daily has nearly 30. Frequency per year is, and it is also the number the
        # manual actually budgets for ("每週 3–4 筆為正常").
        years = max((e.entry_time.max() - e.entry_time.min()).days / 365.25, 0.5) \
            if len(e) else 1.0
        n_run = 0
        for f in fcfg:
            # A resting order fills intrabar, so you cannot decline it based on how
            # that bar ends up closing. Only `market` — where the entry IS the
            # confirmation close — may vary the confirmation type. `limit_confirm`
            # already pays for a missing rejection by scratching the position.
            if s["entry_mode"] != "market" and f["confirm"] != "any":
                continue
            sel = B.sequence(e[_mask(e, f)])
            st = B.r_stats(sel)
            ins = B.r_stats(sel[sel.entry_time < split]) if len(sel) else B.r_stats(sel)
            oos = B.r_stats(sel[sel.entry_time >= split]) if len(sel) else B.r_stats(sel)
            rows.append({**s, **f, **st,
                         "years": round(years, 2),
                         "tpy": round(st["trades"] / years, 2),
                         "r_per_year": round(st["total_r"] / years, 3),
                         "is_trades": ins["trades"], "is_exp": ins["expectancy"],
                         "oos_trades": oos["trades"], "oos_exp": oos["expectancy"],
                         "oos_pf": oos["profit_factor"]})
            n_run += 1
        print(f"[{si:2d}/{len(scfg)}] tf={s['tf']:3s} {s['entry_mode']:13s} "
              f"sl={s['sl_mode']:5s} ms={s['min_swing']} ba={s['band_atr']}  "
              f"cands={len(e):5d}  split={str(split)[:10]}  runs={n_run}  "
              f"({time.time()-t0:.0f}s)")

    sw = pd.DataFrame(rows)
    sw.to_csv(os.path.join(HERE, "sweep.csv"), index=False)
    allc = pd.concat(keep_cands, ignore_index=True)
    allc.to_parquet(os.path.join(HERE, "candidates.parquet"), index=False)

    print(f"\n{len(sw)} runs -> sweep.csv   ({time.time()-t0:.0f}s)")
    print("\nmean full-sample expectancy by execution timeframe:")
    print(sw.groupby("tf").agg(runs=("expectancy", "size"), exp=("expectancy", "mean"),
                               oos=("oos_exp", "mean"), trades=("trades", "mean"))
          .round(3).to_string())
    ok = sw[(sw.is_trades >= 50) & (sw.oos_trades >= 25)]
    cols = ["tf", "entry_mode", "sl_mode", "min_swing", "band_atr", "min_conf",
            "min_rr", "sl_atr_mult", "max_legs", "mtf", "confirm", "trades",
            "expectancy", "win_rate", "profit_factor", "tstat", "is_exp",
            "oos_trades", "oos_exp"]
    print(f"\ntop 15 by IN-SAMPLE expectancy, with their OOS result "
          f"({len(ok)} eligible):")
    print(ok.sort_values("is_exp", ascending=False)[cols].head(15).to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))
    return sw


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
