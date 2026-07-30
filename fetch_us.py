"""Download an equity universe — US (S&P 500) or TW (screened TWSE listings).

A different shape of problem from FX: hundreds of tickers instead of 46, but Yahoo
serves equities in batches, so a whole index at fifteen years of daily bars arrives
in about a minute. Hourly is still capped at 730 days — that cap is Yahoo's, not a
setting, so the 4H ladder can only ever be tested over ~2.8 years however much
history the daily ladder gets.

    python fetch_us.py                     # US: constituents + 15y daily + 730d 1h
    python fetch_us.py --market tw         # TW: same, from TWSE open data
    python fetch_us.py --daily             # skip hourly (daily ladder only)
"""
import os
import sys
import json
import time
import warnings

import pandas as pd
import yfinance as yf

import config as C
import markets as M
from fetch import _clean, resample, _write

warnings.filterwarnings("ignore")

SP500_CSV = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
             "main/data/constituents.csv")
BATCH = 40


def constituents():
    """The market's tradeable list, cached to disk so a later run needs no network."""
    path = os.path.join(C.DATA, "universe.json")

    if C.MARKET == "tw":
        try:
            uni = M.tw_fetch_universe()
            if len(uni) > 100:
                os.makedirs(C.DATA, exist_ok=True)
                json.dump(uni, open(path, "w", encoding="utf-8"), ensure_ascii=False)
                print(f"TWSE listed commons after turnover screen: {len(uni)} (fetched)")
                return uni
        except Exception as e:
            print(f"  ! TWSE fetch failed ({str(e)[:60]}), using built-in list")
        uni = M.tw_universe()
        print(f"TW universe: {len(uni)} tickers (cached/fallback)")
        return uni

    try:
        d = pd.read_csv(SP500_CSV)
        col = "Symbol" if "Symbol" in d.columns else d.columns[0]
        nm = "Security" if "Security" in d.columns else col
        # Yahoo writes class shares with a dash: BRK.B -> BRK-B
        uni = {str(s).replace(".", "-"): str(n)
               for s, n in zip(d[col], d[nm]) if isinstance(s, str)}
        if len(uni) > 400:
            os.makedirs(C.DATA, exist_ok=True)
            json.dump(uni, open(path, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"S&P 500 constituents: {len(uni)} tickers (fetched)")
            return uni
    except Exception as e:
        print(f"  ! constituent fetch failed ({str(e)[:60]}), using fallback")
    uni = M.us_universe()
    print(f"S&P 500 constituents: {len(uni)} tickers (fallback/cached)")
    return uni


def _split(raw, syms):
    """yfinance group_by='ticker' hands back a MultiIndex; peel one frame per name."""
    out = {}
    for s in syms:
        try:
            d = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        d = d.dropna(how="all")
        if len(d) > 200:
            out[s] = d
    return out


def grab(syms, period, interval):
    got = {}
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        try:
            raw = yf.download(chunk, period=period, interval=interval, progress=False,
                              auto_adjust=False, threads=True, group_by="ticker")
            got.update(_split(raw, chunk))
        except Exception as e:
            print(f"  ! batch {i // BATCH + 1}: {str(e)[:70]}")
        print(f"    {interval} {min(i + BATCH, len(syms)):4d}/{len(syms)}  "
              f"got {len(got)}", end="\r", flush=True)
        time.sleep(0.4)
    print()
    return got


def main():
    a = sys.argv[1:]
    C.set_market(a[a.index("--market") + 1] if "--market" in a else "us")
    os.makedirs(C.DATA, exist_ok=True)
    uni = constituents()
    syms = list(uni)
    daily_only = "--daily" in a

    print(f"\n[{C.MARKET}] daily 15y for {len(syms)} tickers")
    d1 = grab(syms, "15y", "1d")
    print("hourly 730d" if not daily_only else "skipping hourly")
    h1 = grab(syms, "730d", "1h") if not daily_only else {}

    meta, ok = {}, 0
    for s in syms:
        if s not in d1:
            continue
        dd = _clean(d1[s])
        if len(dd) < 500:
            continue
        n = {"1d": _write(dd, "1d", s),
             "1w": _write(resample(dd, "W-MON"), "1w", s),
             "1mo": _write(resample(dd, "MS"), "1mo", s)}
        if s in h1:
            hh = _clean(h1[s])
            if len(hh) > 1500:
                n["1h"] = _write(hh, "1h", s)
                n["4h"] = _write(resample(hh, "4h"), "4h", s)
        n.setdefault("1h", 0)
        n.setdefault("4h", 0)
        meta[s] = {"name": uni[s], "bars": n,
                   "start": str(dd.index[0]), "end": str(dd.index[-1]),
                   "d_start": str(dd.index[0]), "d_end": str(dd.index[-1])}
        ok += 1

    json.dump(meta, open(os.path.join(C.DATA, "meta.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    tot = {tf: sum(m["bars"].get(tf, 0) for m in meta.values())
           for tf in ("1h", "4h", "1d", "1w", "1mo")}
    print(f"\n{ok}/{len(syms)} tickers cached into {C.DATA}")
    print("  total bars: " + "  ".join(f"{k}={v:,}" for k, v in tot.items()))
    if meta:
        any_m = next(iter(meta.values()))
        print(f"  daily history from {any_m['d_start'][:10]}")
    return 0 if ok > 50 else 1


if __name__ == "__main__":
    sys.exit(main())
