"""Download the bars every timeframe stack needs.

Two separate pulls, because Yahoo treats them differently:
  * 1H — capped at ~730 days. 4H is RESAMPLED from it so the two intraday frames
    agree bar-for-bar, which matters when the premise is cross-timeframe alignment.
  * Daily — no such cap, so we take the full history (two decades) and resample
    weekly and monthly from it. That is what makes a daily-execution backtest worth
    running at all: 1H gives ~2.8 years of context, daily gives ~20.
"""
import os, sys, time, json, warnings
import pandas as pd
import yfinance as yf

from config import DATA, SYMBOLS, UNIVERSE

warnings.filterwarnings("ignore")


def _download(sym, interval="1h", period="730d", tries=3):
    for k in range(tries):
        try:
            d = yf.download(sym, period=period, interval=interval, progress=False,
                            auto_adjust=False, threads=False)
            if d is not None and len(d):
                return d
        except Exception as e:
            print(f"    ! {sym} {interval} attempt {k+1}: {str(e)[:70]}")
        time.sleep(2 + 3 * k)
    return None


def _clean(d):
    """Flatten yfinance's MultiIndex columns, force UTC, drop dead weekend bars."""
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].copy()
    d.index = pd.to_datetime(d.index, utc=True)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.dropna()
    # Yahoo pads the weekend with flat synthetic bars; they have zero range and
    # would otherwise be counted as legitimate inside-bars by the structure code.
    rng = d.high - d.low
    d = d[rng > 0]
    return d


def resample(d, rule):
    """Aggregate to a higher timeframe, left-labelled so the stamp is the bar OPEN."""
    kw = {"label": "left", "closed": "left"}
    if rule[0].isdigit():        # fixed offsets ("4h") can be pinned to the epoch;
        kw["origin"] = "epoch"   # anchored ones ("W-MON", "MS") define their own grid
    o = d.resample(rule, **kw).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return o.dropna()


def _write(d, tf, sym):
    out = os.path.join(DATA, tf)
    os.makedirs(out, exist_ok=True)
    d = d.reset_index()
    d.columns = ["date"] + list(d.columns[1:])
    d.to_csv(os.path.join(out, sym.replace("=", "_") + ".csv"), index=False)
    return len(d)


def _merge(old, new):
    """Append new bars onto the cached series; the newest copy of a stamp wins."""
    d = pd.concat([old, new])
    return d[~d.index.duplicated(keep="last")].sort_index()


def update(sym):
    """Incremental refresh — pull only the recent tail and re-derive the rest.

    The watcher runs this every hour, so re-downloading two decades of daily bars 46
    times an hour is out of the question. 1H needs a few days of overlap (Yahoo
    revises the most recent bars); daily needs a month.
    """
    p1 = os.path.join(DATA, "1h", sym.replace("=", "_") + ".csv")
    pd_ = os.path.join(DATA, "1d", sym.replace("=", "_") + ".csv")
    if not (os.path.exists(p1) and os.path.exists(pd_)):
        return None

    def _load(p):
        d = pd.read_csv(p, parse_dates=["date"]).set_index("date")
        d.index = pd.to_datetime(d.index, utc=True)
        return d

    h1, d1 = _load(p1), _load(pd_)
    n1 = _download(sym, "1h", "7d", tries=2)
    nd = _download(sym, "1d", "1mo", tries=2)
    if n1 is None and nd is None:
        return None
    if n1 is not None:
        h1 = _merge(h1, _clean(n1))
    if nd is not None:
        d1 = _merge(d1, _clean(nd))

    n = {"1h": _write(h1, "1h", sym),
         "4h": _write(resample(h1, "4h"), "4h", sym),
         "1d": _write(d1, "1d", sym),
         "1w": _write(resample(d1, "W-MON"), "1w", sym),
         "1mo": _write(resample(d1, "MS"), "1mo", sym)}
    return {"bars": n, "end": str(h1.index[-1]), "d_end": str(d1.index[-1])}


def _cached(sym):
    """All five frames already on disk? Lets a universe expansion be incremental."""
    return all(os.path.exists(os.path.join(DATA, tf, sym.replace("=", "_") + ".csv"))
               for tf in ("1h", "4h", "1d", "1w", "1mo"))


def main():
    os.makedirs(DATA, exist_ok=True)
    force = "--force" in sys.argv
    mpath = os.path.join(DATA, "meta.json")
    meta = json.load(open(mpath)) if os.path.exists(mpath) and not force else {}

    if "--update" in sys.argv:                 # incremental path, used by watch.py
        ok = 0
        for sym in SYMBOLS:
            u = update(sym)
            if u and sym in meta:
                meta[sym].update(u)
                ok += 1
            time.sleep(0.15)
        json.dump(meta, open(mpath, "w"), indent=1)
        newest = max((m.get("end", "") for m in meta.values()), default="?")
        print(f"updated {ok}/{len(SYMBOLS)} pairs, newest 1h bar {newest[:16]}")
        return 0 if ok else 1

    ok, bad, skipped = 0, [], 0
    for i, sym in enumerate(SYMBOLS, 1):
        if not force and sym in meta and _cached(sym):
            skipped += 1
            ok += 1
            continue
        print(f"[{i:2d}/{len(SYMBOLS)}] {UNIVERSE[sym]:9s} ", end="", flush=True)
        raw = _download(sym, "1h", "730d")
        rawd = _download(sym, "1d", "max")
        if raw is None or len(raw) < 2000 or rawd is None or len(rawd) < 1000:
            print("FAILED / too short")
            bad.append(sym)
            continue
        h1, d1 = _clean(raw), _clean(rawd)
        n = {"1h": _write(h1, "1h", sym),
             "4h": _write(resample(h1, "4h"), "4h", sym),
             "1d": _write(d1, "1d", sym),
             # weeks run Monday→Sunday; the FX week actually opens Sunday evening but
             # that bar is a stub, and folding it into Monday keeps the count honest
             "1w": _write(resample(d1, "W-MON"), "1w", sym),
             "1mo": _write(resample(d1, "MS"), "1mo", sym)}
        meta[sym] = {"name": UNIVERSE[sym], "bars": n,
                     "start": str(h1.index[0]), "end": str(h1.index[-1]),
                     "d_start": str(d1.index[0]), "d_end": str(d1.index[-1])}
        ok += 1
        print(f"1h={n['1h']:6d} 4h={n['4h']:5d} 1d={n['1d']:5d} 1w={n['1w']:4d} "
              f"1mo={n['1mo']:3d}   daily from {d1.index[0].date()}")
        time.sleep(0.4)

    meta = {s: meta[s] for s in SYMBOLS if s in meta}     # drop pairs no longer listed
    json.dump(meta, open(mpath, "w"), indent=1)
    tot = {tf: sum(m["bars"][tf] for m in meta.values())
           for tf in ("1h", "4h", "1d", "1w", "1mo")}
    print(f"\n{ok}/{len(SYMBOLS)} pairs cached ({skipped} already present) into {DATA}")
    print("  total bars: " + "  ".join(f"{k}={v:,}" for k, v in tot.items()))
    if bad:
        print("missing:", ", ".join(bad))
    return 0 if ok >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
