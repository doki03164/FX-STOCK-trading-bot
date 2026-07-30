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

    if C.MARKET == "cm":
        uni = M.cm_universe()
        os.makedirs(C.DATA, exist_ok=True)
        json.dump(uni, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"Commodities & metals: {len(uni)} instruments "
              f"({len(M.CM_DIRECT)} direct + {len(M.CM_DERIVED)} derived)")
        return uni

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


def _split(raw, syms, min_rows=200):
    """yfinance group_by='ticker' hands back a MultiIndex; peel one frame per name.

    `min_rows` guards against a half-empty first download. It must be small on an
    incremental top-up — a 3-month daily pull is ~60 rows, and a 200-row floor threw
    every symbol away while still reporting success.
    """
    out = {}
    for s in syms:
        try:
            d = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        d = d.dropna(how="all")
        if len(d) >= min_rows:
            out[s] = d
    return out


def grab(syms, period, interval, min_rows=200):
    got = {}
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        try:
            raw = yf.download(chunk, period=period, interval=interval, progress=False,
                              auto_adjust=False, threads=True, group_by="ticker")
            got.update(_split(raw, chunk, min_rows))
        except Exception as e:
            print(f"  ! batch {i // BATCH + 1}: {str(e)[:70]}")
        print(f"    {interval} {min(i + BATCH, len(syms)):4d}/{len(syms)}  "
              f"got {len(got)}", end="\r", flush=True)
        time.sleep(0.4)
    print()
    return got


def derive(uni):
    """Build the synthetic gold/silver crosses Yahoo does not carry.

    XAU/EUR is XAU/USD divided by EUR/USD — the same arithmetic a broker uses. The
    numerator comes from this market, the denominator from the FX book, joined as-of
    so a missing FX bar never invents a price.
    """
    import loader
    made = 0
    for sym, (name, num, den, op) in M.CM_DERIVED.items():
        if sym not in uni:
            continue
        try:
            a = loader.bars(num, "1d", "cm").set_index("date")
            C.set_market("fx"); b = loader.bars(den, "1d", "fx").set_index("date")
            C.set_market("cm")
        except FileNotFoundError:
            print(f"  ! {sym}: need {num} and {den} first")
            continue
        j = a.join(b, how="inner", rsuffix="_d").dropna()
        if len(j) < 500:
            continue
        f = (1.0 / j.close_d) if op == "div" else j.close_d
        d = pd.DataFrame({"open": j.open * f, "high": j.high * f,
                          "low": j.low * f, "close": j.close * f}, index=j.index)
        d = d.assign(high=d[["high", "low"]].max(axis=1),
                     low=d[["high", "low"]].min(axis=1))
        _write(d, "1d", sym)
        _write(resample(d, "W-MON"), "1w", sym)
        _write(resample(d, "MS"), "1mo", sym)
        made += 1
    print(f"  derived {made} synthetic crosses")
    return made


def update():
    """Batch incremental refresh — the reason live equity/commodity plans went stale.

    fetch.py's per-symbol update() would take an hour on 500 names, so app.py used to
    skip non-FX markets entirely and their bars never moved after the first download.
    A 4H plan built on week-old bars is worse than no plan. This pulls the recent tail
    for the whole universe in batches and re-derives the higher frames.
    """
    uni = C.UNIVERSE
    syms = [s for s in uni if s not in M.CM_DERIVED]
    print(f"[{C.MARKET}] incremental update, {len(syms)} symbols")
    d1 = grab(syms, "3mo", "1d", min_rows=5)
    h1 = grab(syms, "7d", "1h", min_rows=5)

    mpath = os.path.join(C.DATA, "meta.json")
    meta = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
    ok = 0
    for s in syms:
        p1 = os.path.join(C.DATA, "1d", s.replace("=", "_") + ".csv")
        if not os.path.exists(p1) or s not in d1:
            continue

        def load(p):
            d = pd.read_csv(p, parse_dates=["date"]).set_index("date")
            d.index = pd.to_datetime(d.index, utc=True)
            return d

        dd = load(p1)
        new = _clean(d1[s])
        dd = pd.concat([dd, new])
        dd = dd[~dd.index.duplicated(keep="last")].sort_index()
        n = {"1d": _write(dd, "1d", s),
             "1w": _write(resample(dd, "W-MON"), "1w", s),
             "1mo": _write(resample(dd, "MS"), "1mo", s)}
        ph = os.path.join(C.DATA, "1h", s.replace("=", "_") + ".csv")
        if s in h1 and os.path.exists(ph):
            hh = pd.concat([load(ph), _clean(h1[s])])
            hh = hh[~hh.index.duplicated(keep="last")].sort_index()
            n["1h"] = _write(hh, "1h", s)
            n["4h"] = _write(resample(hh, "4h"), "4h", s)
        if s in meta:
            meta[s]["bars"] = {**meta[s].get("bars", {}), **n}
            meta[s]["end"] = str(dd.index[-1])
            meta[s]["d_end"] = str(dd.index[-1])
        ok += 1
    if M.CM_DERIVED and C.MARKET == "cm":
        derive(uni)
    json.dump(meta, open(mpath, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    newest = max((m.get("d_end", "") for m in meta.values()), default="?")
    print(f"updated {ok}/{len(syms)}, newest daily bar {newest[:10]}")
    return 0 if ok >= max(1, len(syms) * 0.5) else 1


def main():
    a = sys.argv[1:]
    C.set_market(a[a.index("--market") + 1] if "--market" in a else "us")
    os.makedirs(C.DATA, exist_ok=True)
    if "--update" in a:
        return update()
    uni = constituents()
    # the derived crosses are computed from other series, not downloaded; asking
    # Yahoo for XAUEUR just earns four 404s and a scary-looking log
    syms = [x for x in uni if x not in M.CM_DERIVED]
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
    if C.MARKET == "cm":
        derive(uni)
        for sym, (name, *_ ) in M.CM_DERIVED.items():
            p = os.path.join(C.DATA, "1d", sym + ".csv")
            if os.path.exists(p):
                meta[sym] = {"name": name, "bars": {"1d": sum(1 for _ in open(p)) - 1,
                             "1h": 0, "4h": 0, "1w": 0, "1mo": 0},
                             "start": "", "end": "", "d_start": "", "d_end": ""}
        json.dump(meta, open(os.path.join(C.DATA, "meta.json"), "w", encoding="utf-8"),
                  indent=1, ensure_ascii=False)
    if meta:
        any_m = next(iter(meta.values()))
        print(f"  daily history from {any_m['d_start'][:10]}")
    # proportional, not absolute: the CM book is 20 instruments and would
    # never clear a hard-coded 50, which made app.py report a phantom failure
    return 0 if ok >= max(5, len(syms) * 0.6) else 1


if __name__ == "__main__":
    sys.exit(main())
