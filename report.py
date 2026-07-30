"""Assemble dashboard_data.json and render the self-contained dashboard.

Reads what sweep.py produced (sweep.csv + candidates.parquet), runs the full
portfolio backtest on the reference configuration, and builds every panel the
page needs — including the whole sweep in columnar form so the heatmap can be
re-pivoted in the browser without a server.

    python report.py
"""
import os, json, sys
import numpy as np
import pandas as pd

import config as C
import markets as M
import gates
import backtest as B
from sweep import _mask, split_at

HERE = C.HERE

# The configuration this dashboard reports on. It is not the single best cell in
# the sweep — it is the centre of the only region where neighbouring cells are all
# positive in BOTH halves of the data. A lone spike surrounded by losers is a
# fitting artefact; a plateau is the closest thing to evidence a sweep can give.
#
# Two deliberate choices come straight out of the sweep:
#   band_atr 1.0 + sl_mode zone -> the stop lands ~2.2xATR away by construction, so
#     E2 (SL >= 2xATR) is satisfied for free rather than filtering trades away.
#   min_rr 1.0 -> E1 relaxed from the manual's 1:2. At 1.5 the count falls off a
#     cliff (420 -> 34 trades) because the target is a fixed structural level.
REFERENCE = {
    "tf": "1h", "min_swing": 3.0, "band_atr": 1.0,
    "entry_mode": "limit", "sl_mode": "zone",
    "min_conf": 4, "min_rr": 1.0, "sl_atr_mult": 2.0, "max_legs": 2,
    "mtf": "daily", "confirm": "any",
}

# Each ladder gets its OWN parameters. Forcing the 1H settings onto 4H and daily is
# what made them look broken — they are different markets in every way that matters
# here (cost per trade, how often a setup appears, how long a hold lasts).
#
# Selected the same way as REFERENCE: gate on "positive in BOTH halves with a usable
# sample", then rank by annual R rather than per-trade expectancy, because the
# complaint about the high timeframes is a shortage of opportunities, not bad trades.
# Both keep band_atr 1.0 so the zone-edge stop still clears E2 (SL >= 2xATR) for free.
LADDERS = {
    "1h": REFERENCE,
    "4h": {
        "tf": "4h", "min_swing": 2.0, "band_atr": 1.0,
        "entry_mode": "limit", "sl_mode": "zone",
        # max_legs 99: on 4H the GATE 2 maturity cap costs trades without adding edge
        "min_conf": 4, "min_rr": 0.5, "sl_atr_mult": 2.0, "max_legs": 99,
        "mtf": "daily+4h", "confirm": "any",
    },
    "1d": {
        "tf": "1d", "min_swing": 3.0, "band_atr": 1.0,
        "entry_mode": "limit_confirm", "sl_mode": "zone",
        "min_conf": 3, "min_rr": 1.0, "sl_atr_mult": 2.0, "max_legs": 99,
        "mtf": "daily", "confirm": "any",
    },
}

# US equities get their own numbers. Same selection rule as FX, run over the S&P
# 500 sweep: 1,653 cells were positive in both halves; this is the centre of that
# region (2,408 trades, IS +0.099 / OOS +0.107, t-stat 3.9). band_atr 1.0 with a
# zone stop again lands the stop ~2.2xATR out, so E2 holds without filtering.
LADDERS_US = {
    "4h": {
        "tf": "4h", "min_swing": 2.0, "band_atr": 1.0,
        "entry_mode": "limit", "sl_mode": "zone",
        "min_conf": 3, "min_rr": 1.0, "sl_atr_mult": 1.5, "max_legs": 99,
        "mtf": "off", "confirm": "any",
    },
    # US daily over 14.9 years: 1,086 cells positive in both halves. This is the
    # highest t-stat among them (4.28 over 2,076 trades) AND the only place in the
    # whole project where the manual's E1 survives as written — a 1:2 reward on a
    # daily equity swing is reachable, which it never was on 1H FX.
    "1d": {
        "tf": "1d", "min_swing": 3.0, "band_atr": 0.75,
        "entry_mode": "limit", "sl_mode": "zone",
        "min_conf": 3, "min_rr": 2.0, "sl_atr_mult": 1.0, "max_legs": 99,
        "mtf": "off", "confirm": "any",
    },
}

# Taiwan, selected from its own 39,600-run daily sweep the same way as the others.
# Read the numbers before trusting them: only 28 of those runs were positive in both
# halves — 0.1%, against 2.7% for US daily — and this one's t-stat is 1.09, not the
# 4.28 the US book manages. Dealing cost is why: 證交稅 0.3% plus commission is 50bps
# round trip, which costs 0.105R of every trade against 0.018R in the US. The edge
# the gates find is roughly the size of the tax.
LADDERS_TW = {
    "1d": {
        "tf": "1d", "min_swing": 3.0, "band_atr": 1.0,
        "entry_mode": "limit", "sl_mode": "zone",
        "min_conf": 3, "min_rr": 1.5, "sl_atr_mult": 1.5, "max_legs": 99,
        "mtf": "daily+4h", "confirm": "any",
    },
}

LADDERS_BY_MARKET = {"fx": LADDERS, "us": LADDERS_US, "tw": LADDERS_TW}


def ladders_for(sw):
    """The configured ladders, minus any timeframe the sweep has no data for.

    A market is often part-built — the US daily sweep runs long after the 4H one —
    and a dashboard that 404s until every timeframe lands is worse than one that
    shows what it has.
    """
    have = set(sw.tf.unique())
    L = LADDERS_BY_MARKET.get(C.MARKET, LADDERS)
    return {k: v for k, v in L.items() if k in have}


# The manual's thresholds exactly as printed in §07, for the side-by-side.
MANUAL = {
    "tf": "1h", "min_swing": 3.0, "band_atr": 0.75,
    "entry_mode": "market", "sl_mode": "wick",
    "min_conf": 3, "min_rr": 2.0, "sl_atr_mult": 2.0, "max_legs": 3,
    "mtf": "daily+4h", "confirm": "any",
}

SKEYS = ["tf", "min_swing", "band_atr", "entry_mode", "sl_mode"]
FKEYS = ["min_conf", "min_rr", "sl_atr_mult", "max_legs", "mtf", "confirm"]


def _structural(cand, cfg):
    m = np.ones(len(cand), bool)
    for k in SKEYS:
        m &= cand[k].values == cfg[k]
    return cand[m]


def funnel(cand, cfg):
    """How many setups each gate threw away — the manual's own scoreboard."""
    e = cand[cand.stage == gates.STAGE["entered"]]
    rows = [
        ("GATE 1–2  結構+成熟度 → 計畫", int((cand.stage >= gates.STAGE["zone"]).sum())),
        ("GATE 4  區域仍未被觸及", int((cand.stage >= gates.STAGE["untouched"]).sum())),
        ("GATE 6  價格抵達區域", int((cand.stage >= gates.STAGE["touched"]).sum())),
        ("GATE 6–7  觸發進場", int(len(e))),
    ]
    on = pd.Series(True, index=e.index)
    b1, b2 = cfg["mtf"] != "off", cfg["mtf"] == "daily+4h"
    seq = [
        ("A1  ≥3 同向標籤", e.nlab >= 3),
        ("A3  趨勢未過成熟", e.legs <= cfg["max_legs"]),
        ("B1  日線階段明確" + ("" if b1 else "（未啟用）"), e.d_ok if b1 else on),
        ("B2  4H 與 1H 同向" + ("" if b2 else "（未啟用）"), e.h4_align if b2 else on),
        ("C2  共振 ≥ %d" % cfg["min_conf"], e.n_conf >= cfg["min_conf"]),
        ("C3  含 25/50 EMA", e.has_ema),
        ("B3  關鍵位空間足夠", e.room_R >= cfg["min_rr"]),
        ("E1  RR ≥ %.1f" % cfg["min_rr"], e.rr >= cfg["min_rr"]),
        ("E2  SL ≥ %.1f×ATR" % cfg["sl_atr_mult"], e.sl_atr >= cfg["sl_atr_mult"]),
    ]
    m = pd.Series(True, index=e.index)
    for name, cond in seq:
        m &= cond.fillna(False)
        rows.append((name, int(m.sum())))
    return rows, e[m]


def heat(t, rows_key, cols_key, rowvals, colvals):
    """expectancy + count grid for a categorical pivot of the trade table."""
    z, n = [], []
    for rv in rowvals:
        zr, nr = [], []
        for cv in colvals:
            s = t[(t[rows_key] == rv) & (t[cols_key] == cv)]
            zr.append(round(float(s.r_net.mean()), 4) if len(s) else None)
            nr.append(int(len(s)))
        z.append(zr)
        n.append(nr)
    return {"z": z, "n": n, "rows": [str(r) for r in rowvals],
            "cols": [str(c) for c in colvals]}


def monthly_heat(curve):
    """Calendar month returns of the account, as year x month."""
    if not len(curve):
        return {"z": [], "rows": [], "cols": []}
    m = curve.resample("ME").last().ffill()
    m = pd.concat([pd.Series([curve.iloc[0]], index=[curve.index[0]]), m])
    r = m.pct_change().dropna()
    years = sorted({d.year for d in r.index})
    z = [[None] * 12 for _ in years]
    for d, v in r.items():
        z[years.index(d.year)][d.month - 1] = round(float(v), 5)
    return {"z": z, "rows": [str(y) for y in years],
            "cols": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}


MIN_EMBED_TRADES = 10


def pack_sweep(sw):
    """Columnar + integer-coded so the whole grid fits in the page without a server.

    Runs with almost no trades are dropped from the EMBED only — they stay in
    sweep.csv. A 3-trade cell tells you nothing and there are tens of thousands of
    them, so they are pure page weight.
    """
    sw = sw[sw.trades >= MIN_EMBED_TRADES]
    keys = SKEYS + FKEYS
    cols, levels = {}, {}
    # timeframes must read fast->slow, not alphabetically ("1d" < "1h" < "4h")
    ORDER = {"tf": {t: i for i, t in enumerate(C.EXEC_TFS)}}
    for k in keys:
        vals = sorted(sw[k].unique(),
                      key=lambda v: (ORDER[k][v],) if k in ORDER
                      else (isinstance(v, str), v))
        levels[k] = [str(v) for v in vals]
        idx = {v: i for i, v in enumerate(vals)}
        cols[k] = [int(idx[v]) for v in sw[k]]
    for k in ("trades", "is_trades", "oos_trades"):
        cols[k] = [int(v) for v in sw[k]]
    for k in ("expectancy", "win_rate", "profit_factor", "tstat", "total_r",
              "max_dd_r", "is_exp", "oos_exp", "tpy", "r_per_year"):
        v = sw[k].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        cols[k] = [round(float(x), 4) for x in v]
    return {"levels": levels, "cols": cols, "n": int(len(sw)),
            "params": keys, "min_trades": MIN_EMBED_TRADES,
            "metrics": ["expectancy", "is_exp", "oos_exp", "win_rate",
                        "profit_factor", "tstat", "r_per_year", "tpy",
                        "total_r", "max_dd_r", "trades"]}


def run_config(cand, cfg, label):
    sub = _structural(cand, cfg)
    rows, kept = funnel(sub, cfg)
    sel = B.sequence(kept)
    tk, curve, st = B.run(sel)
    # split on the trades the account actually took, not on the pre-portfolio list,
    # so the IS/OOS numbers describe the same equity curve shown above them
    src = tk if len(tk) else sel
    split = split_at(sub[sub.stage == gates.STAGE["entered"]])
    ins = B.r_stats(src[src.entry_time < split]) if len(src) else B.r_stats(src)
    oos = B.r_stats(src[src.entry_time >= split]) if len(src) else B.r_stats(src)
    return {
        "label": label, "params": cfg, "funnel": rows, "split": str(split)[:10],
        "stats": {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                  for k, v in st.items() if k != "rejects"},
        "rejects": st.get("rejects", {}),
        "is": ins, "oos": oos,
        "curve": [[str(t), round(float(v), 2)] for t, v in curve.items()],
        "monthly": monthly_heat(curve),
        "trades": tk, "selected": sel,
    }


def tf_compare(cand, sw, cfgs):
    """Each ladder on its own parameters, plus the whole grid behind it.

    Two views per timeframe: its own tuned configuration, and the trade-weighted
    average over every sweep cell with a usable sample. One config can be luck; the
    grid average cannot.
    """
    out = []
    for tf in cfgs:
        c = cfgs[tf]
        sub = _structural(cand, c)
        e = sub[sub.stage == gates.STAGE["entered"]]
        sel = B.sequence(e[_mask(e, c)])
        st = B.r_stats(sel)
        split = split_at(e)
        g = sw[(sw.tf == tf) & (sw.trades >= 40)]
        w = g.trades.sum()
        # full account run per timeframe, so the bar chart compares money, not just R
        tk, curve, ast = B.run(sel)
        yrs = max(ast.get("days", 1) / 365.25, 0.1)
        out.append({
            "params": c,
            "cagr": round(float(ast.get("cagr", 0.0)), 4),
            "total_return": round(float(ast.get("total_return", 0.0)), 4),
            "max_dd": round(float(ast.get("max_dd", 0.0)), 4),
            "sharpe": round(float(ast.get("sharpe", 0.0)), 3),
            "years": round(yrs, 2),
            "tpy": round(st["trades"] / max((e.entry_time.max() - e.entry_time.min()).days
                                            / 365.25, 0.5), 2) if len(e) else 0.0,
            "r_per_year": round(st["total_r"] / yrs, 2),
            "curve": [[str(t)[:10], round(float(v) / float(curve.iloc[0]), 4)]
                      for t, v in curve.items()] if len(curve) else [],
            "tf": tf, "label": C.TF_LABEL[tf],
            "mid": C.TF_LABEL[C.TF_STACK[tf]["mid"]],
            "high": C.TF_LABEL[C.TF_STACK[tf]["high"]],
            "bars": int(len(sub.plan_i)) and int(sub.plan_i.max()),
            "span": [str(e.entry_time.min())[:10], str(e.entry_time.max())[:10]]
                    if len(e) else ["–", "–"],
            "candidates": int(len(e)),
            "cost_r": round(float(e.cost_r.median()), 4) if len(e) else None,
            "leg_atr": round(float(e.leg_atr.median()), 2) if len(e) else None,
            "rr_med": round(float(e.rr.median()), 2) if len(e) else None,
            "sl_atr_med": round(float(e.sl_atr.median()), 2) if len(e) else None,
            "trades": st["trades"], "expectancy": round(st["expectancy"], 4),
            "win_rate": round(st["win_rate"], 4),
            "profit_factor": round(min(st["profit_factor"], 99), 3),
            "tstat": round(st["tstat"], 3),
            "is_exp": round(B.r_stats(sel[sel.entry_time < split])["expectancy"], 4)
                      if len(sel) else 0.0,
            "oos_exp": round(B.r_stats(sel[sel.entry_time >= split])["expectancy"], 4)
                       if len(sel) else 0.0,
            "grid_cells": int(len(g)),
            "grid_exp": round(float(np.average(g.expectancy, weights=g.trades)), 4) if w else None,
            "grid_oos": round(float(np.average(g.oos_exp, weights=g.trades)), 4) if w else None,
            "grid_corr": round(float(g.is_exp.corr(g.oos_exp)), 3) if len(g) > 2 else None,
        })
    return out


def ladder_runs(cand, LAD):
    """Each ladder as its own account, plus all three sharing one account.

    The combined run is the point of having three ladders: 4H and daily are starved
    of opportunities on their own (5–50 trades a year), but they fire at different
    times from the 1H book, so stacking them on one account raises the trade count
    without raising risk per trade. Correlation and margin limits still apply ACROSS
    timeframes, which is what stops this from being three times the leverage.
    """
    out, pooled = [], []
    for tf, cfg in LAD.items():
        sub = _structural(cand, cfg)
        e = sub[sub.stage == gates.STAGE["entered"]]
        sel = B.sequence(e[_mask(e, cfg)]).copy()
        sel["tf"] = tf
        pooled.append(sel)
        out.append(_acct(sel, C.TF_LABEL[tf], tf, split_at(e)))

    # The four curves are only comparable over the window all three ladders exist in.
    # Daily reaches back to 1997 and 1H only to 2023, so a combined CAGR measured over
    # the union would be a daily-only CAGR with 2.8 years of everything else stapled on.
    start = max(p.entry_time.min() for p in pooled if len(p))
    common = []
    for p, tf in zip(pooled, LAD):
        s = p[p.entry_time >= start]
        common.append(_acct(s, C.TF_LABEL[tf], tf, split_at(s)))
    allsel = pd.concat([p[p.entry_time >= start] for p in pooled], ignore_index=True)
    # one pair may only be held once at a time regardless of which ladder found it
    allsel = B.sequence(allsel.sort_values("entry_time"))
    common.append(_acct(allsel, "三週期組合", "all", split_at(allsel)))
    return out, common, str(start)[:10]


def _acct(sel, label, tf, split):
    tk, curve, st = B.run(sel)
    src = tk if len(tk) else sel
    years = max(st.get("days", 1) / 365.25, 0.1)
    r = B.r_stats(sel)
    return {
        "tf": tf, "label": label, "trades": st.get("trades", 0),
        "tpy": round(st.get("trades", 0) / years, 1),
        "expectancy": round(r["expectancy"], 4), "win_rate": round(r["win_rate"], 4),
        "profit_factor": round(min(r["profit_factor"], 99), 3),
        "r_per_year": round(r["total_r"] / years, 2),
        "cagr": round(float(st.get("cagr", 0.0)), 4),
        "total_return": round(float(st.get("total_return", 0.0)), 4),
        "max_dd": round(float(st.get("max_dd", 0.0)), 4),
        "sharpe": round(float(st.get("sharpe", 0.0)), 3),
        "years": round(years, 2),
        "is_exp": round(B.r_stats(src[src.entry_time < split])["expectancy"], 4)
                  if len(src) else 0.0,
        "oos_exp": round(B.r_stats(src[src.entry_time >= split])["expectancy"], 4)
                   if len(src) else 0.0,
        "rejects": st.get("rejects", {}),
        "curve": [[str(t)[:10], round(float(v), 1)] for t, v in curve.items()],
    }


def e1_cliff(cand, cfg):
    """How fast the trade count collapses as E1 is tightened, holding all else equal.

    This is the single most load-bearing number on the page, so it is measured, not
    typed in — the manual's 1:2 lands past the end of this list.
    """
    sub = _structural(cand, cfg)
    e = sub[sub.stage == gates.STAGE["entered"]]
    out = []
    for rr in (0.5, 1.0, 1.5, 2.0, 2.5):
        sel = B.sequence(e[_mask(e, dict(cfg, min_rr=rr))])
        st = B.r_stats(sel)
        out.append({"rr": rr, "trades": st["trades"],
                    "expectancy": round(st["expectancy"], 4)})
    return out


def main():
    sw = pd.read_csv(C.art("sweep","csv"))
    cand = pd.read_parquet(C.art("candidates","parquet"))

    _lad = ladders_for(sw)
    _ref = REFERENCE if REFERENCE["tf"] in _lad else next(iter(_lad.values()))
    _man = dict(MANUAL, tf=_ref["tf"])
    ref = run_config(cand, _ref, "Reference (sweep plateau)")
    man = run_config(cand, _man, "Manual §07 as written")
    LAD = ladders_for(sw)
    tfc = tf_compare(cand, sw, LAD)
    ladders, common, common_start = ladder_runs(cand, LAD)

    tk, sel = ref.pop("trades"), ref.pop("selected")
    man.pop("trades"), man.pop("selected")

    # --- heatmaps built from the reference config's accepted trades
    sel = sel.copy()
    sel["side"] = np.where(sel.dr > 0, "long", "short")
    sel["pair"] = [C.UNIVERSE[s] for s in sel.symbol]
    pairs = sorted(sel.pair.unique())
    bysym = heat(sel, "pair", "side", pairs, ["long", "short"])

    # --- robustness: does an in-sample winner stay a winner?
    ok = sw[(sw.is_trades >= 60) & (sw.oos_trades >= 30)]
    top = ok.sort_values("is_exp", ascending=False).head(25)
    rob = {
        "eligible": int(len(ok)),
        "corr_is_oos": round(float(ok.is_exp.corr(ok.oos_exp)), 4),
        "top25_is": round(float(top.is_exp.mean()), 4),
        "top25_oos": round(float(top.oos_exp.mean()), 4),
        "all_oos": round(float(ok.oos_exp.mean()), 4),
        "both_positive": int(((sw.is_trades >= 60) & (sw.oos_trades >= 30) &
                              (sw.is_exp > 0.05) & (sw.oos_exp > 0.05)).sum()),
        "by_tf": {tf: round(float(g.is_exp.corr(g.oos_exp)), 3)
                  for tf, g in ok.groupby("tf") if len(g) > 2},
        "scatter": [[round(float(a), 3), round(float(b), 3), t]
                    for a, b, t in zip(ok.is_exp, ok.oos_exp, ok.tf)][:3000],
    }

    meta = json.load(open(os.path.join(C.DATA, "meta.json"), encoding="utf-8"))
    any_m = next(iter(meta.values()))
    bars_total = {tf: int(sum(m["bars"][tf] for m in meta.values()))
                  for tf in C.ALL_TFS}
    trades_out = tk.copy()
    for c in ("entry_time", "exit_time", "plan_time"):
        trades_out[c] = trades_out[c].astype(str)

    data = {
        "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "market": C.MARKET, "market_name": M.MARKETS[C.MARKET]["name"],
        "window": {"start": any_m["start"][:10], "end": any_m["end"][:10],
                   "d_start": any_m["d_start"][:10], "split": ref["split"]},
        "bars": bars_total,
        "stack": {tf: [C.TF_LABEL[tf], C.TF_LABEL[v["mid"]], C.TF_LABEL[v["high"]]]
                  for tf, v in C.TF_STACK.items()},
        "universe": [{"sym": s, "name": n} for s, n in C.UNIVERSE.items()],
        "costs": {s: round(C.cost_pips(s), 2) for s in C.SYMBOLS},
        "account": C.ACCOUNT,
        "reference": ref, "manual": man, "tf_compare": tfc,
        "ladders": ladders, "ladder_params": LAD,
        "common": common, "common_start": common_start,
        "e1_cliff": e1_cliff(cand, _ref),
        "sweep": pack_sweep(sw), "sweep_total": int(len(sw)),
        "robustness": rob,
        "heat_pair": bysym,
        "trades": json.loads(trades_out[[
            "entry_time", "exit_time", "symbol", "dr", "entry", "sl", "tp",
            "rr", "sl_atr", "n_conf", "legs", "risk_pips", "r_net", "pnl",
            "exit_equity", "exit_why"]].to_json(orient="records")),
    }

    plan = C.art("plan","json")
    if os.path.exists(plan):
        data["plan"] = json.load(open(plan, encoding="utf-8"))

    # watch.py's transition log — the newest 60 are enough to see the last few days
    evp = C.art("events","jsonl")
    if os.path.exists(evp):
        rows = [json.loads(x) for x in open(evp, encoding="utf-8") if x.strip()]
        data["events"] = rows[-60:][::-1]

    out = C.art("dashboard_data","json")
    json.dump(data, open(out, "w", encoding="utf-8"), allow_nan=False)
    print(f"wrote {out}  ({os.path.getsize(out)/1024:.0f} KB)")

    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    blob = json.dumps(data, allow_nan=False).replace("</script", "<\\/script")
    html = tpl.replace("/*__DATA__*/", blob)
    path = C.art("dashboard","html")
    open(path, "w", encoding="utf-8").write(html)
    print(f"wrote {path}  ({os.path.getsize(path)/1024:.0f} KB)")

    s = ref["stats"]
    print(f"\nreference ({REFERENCE['tf']}): {s['trades']} trades  "
          f"exp {s['expectancy_r']:+.3f}R  return {s['total_return']*100:+.1f}%  "
          f"maxDD {s['max_dd']*100:.1f}%  PF {s['profit_factor']:.2f}")
    print(f"           IS {ref['is']['trades']} @ {ref['is']['expectancy']:+.3f}R | "
          f"OOS {ref['oos']['trades']} @ {ref['oos']['expectancy']:+.3f}R")
    print(f"manual §07: {man['stats'].get('trades', 0)} trades survive all 18 preconditions")
    for title, rows in (("full history, each ladder alone", ladders),
                        (f"common window from {common_start}", common)):
        print(f"\n{title} (1% risk):")
        for t in rows:
            print(f"  {t['label']:8s} {t['trades']:5d} trades ({t['tpy']:6.1f}/yr)  "
                  f"exp {t['expectancy']:+.3f}R  R/yr {t['r_per_year']:+6.2f}  "
                  f"CAGR {t['cagr']*100:+6.2f}%  maxDD {t['max_dd']*100:6.1f}%  "
                  f"IS {t['is_exp']:+.3f} OOS {t['oos_exp']:+.3f}")


if __name__ == "__main__":
    _a = sys.argv[1:]
    C.set_market(_a[_a.index("--market") + 1] if "--market" in _a else "fx")
    sys.exit(main())
