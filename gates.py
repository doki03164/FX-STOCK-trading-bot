"""GATE 1–7 of 交易操作手冊, as a candidate generator.

Candidates are generated with the gates wide open and every threshold RECORDED as
a column instead of applied. That is deliberate: it makes `min_conf`, `min_rr`,
`max_legs`, `sl_atr_mult`, the MTF rules and the confirmation type pure boolean
masks over one table, so a 3,000-run parameter sweep costs one generation pass
rather than 3,000 of them. The manual's own defaults are just one such mask.

What is NOT deferrable — and so is baked in here — is anything that changes the
shape of a trade: where the zone sits, when price reached it, which bar confirmed,
and what happened afterwards.
"""
import numpy as np
import pandas as pd

import config as C
import structure as S
from loader import frame, htf_barrier

# how far a candidate got before it died — drives the dashboard funnel
STAGE = {"plan": 1, "zone": 2, "untouched": 3, "touched": 4, "entered": 5}

PLAN_TTL = 120      # bars a plan stays live waiting for price to arrive
MAX_HOLD = 400      # ~3 trading weeks on 1H before a stuck trade is marked out


def _confirm(o, h, l, c, j, dr, wick_frac):
    """(wick, engulf, pin) rejection flags for a CLOSED bar, in trend direction."""
    rng = h[j] - l[j]
    if rng <= 0:
        return False, False, False
    body = abs(c[j] - o[j])
    mid = (h[j] + l[j]) / 2
    if dr > 0:
        wick = min(o[j], c[j]) - l[j]
        ok_side = c[j] > mid
        eng = (c[j] > o[j]) and (c[j - 1] < o[j - 1]) and (c[j] >= o[j - 1]) and (o[j] <= c[j - 1])
    else:
        wick = h[j] - max(o[j], c[j])
        ok_side = c[j] < mid
        eng = (c[j] < o[j]) and (c[j - 1] > o[j - 1]) and (c[j] <= o[j - 1]) and (o[j] >= c[j - 1])
    w = (wick / rng >= wick_frac) and ok_side
    pin = (body / rng <= 0.35) and (wick / rng >= 0.5) and ok_side
    return bool(w), bool(eng), bool(pin)


def _simulate(o, h, l, c, i, entry, sl, tp, dr, start=None, atr=None,
              tp_mode="structure", trail_atr=2.0):
    """Walk forward to the exit. Both stop and target in one bar counts as the loss.

    Stops are gap-aware (filled at the open when price jumped past them); targets
    are not (filled exactly at the limit). Pessimistic on both sides on purpose.

    `tp_mode` is the manual's §09 escape hatch — 已回測驗證的管理法. The default keeps
    §06's rule (TP = 前一個結構高/低點), which caps reward at whatever the last swing
    happens to offer. `trail` drops the fixed target and ratchets the stop instead, so
    a trend that keeps going keeps paying; it is the only way to earn more than the
    prior extreme allows.
    """
    n = len(c)
    peak = entry
    for j in range(i + 1 if start is None else start, min(n, i + MAX_HOLD)):
        if dr > 0:
            if l[j] <= sl:
                return j, min(o[j], sl), "sl"
            if tp_mode != "trail" and h[j] >= tp:
                return j, tp, "tp"
            if tp_mode == "trail":
                peak = max(peak, h[j])
                if atr is not None and np.isfinite(atr[j]):
                    sl = max(sl, peak - trail_atr * atr[j])   # never loosens
        else:
            if h[j] >= sl:
                return j, max(o[j], sl), "sl"
            if tp_mode != "trail" and l[j] <= tp:
                return j, tp, "tp"
            if tp_mode == "trail":
                peak = min(peak, l[j])
                if atr is not None and np.isfinite(atr[j]):
                    sl = min(sl, peak + trail_atr * atr[j])
    j = min(n - 1, i + MAX_HOLD)
    return j, c[j], "time"


def scan(sym, tf="1h", swing_n=5, min_swing=2.0, band_atr=0.5, entry_mode="market",
         sl_mode="wick", tp_mode="structure", trail_atr=2.0, wick_frac=0.40,
         max_wait=24, sr_lookback=600, sr_touches=2, tl_touches=3, sl_buffer=0.2,
         min_retrace=0.236, max_retrace=0.886):
    """Every candidate trade in one pair's history, with its gate attributes.

    `tf` is the execution timeframe; its two context frames come from TF_STACK, so
    the manual's 日線/4H/1H division of labour slides up the ladder intact. Every
    window below is measured in BARS, which keeps the rules scale-invariant — the
    same code reads a 1H pullback and a daily one the same way.

    `entry_mode` is GATE 7's 進場方式二選一:
      market — wait at the screen for a closed rejection bar, buy its close
      limit  — 掛單: rest an order at the near edge of the zone and walk away
    They are genuinely different strategies (the limit fills earlier and cheaper
    but skips the confirmation evidence), so the sweep treats it as an axis.

    `sl_mode` reads "SL = HL低點 − 0.2×ATR" two ways: `wick` puts it under the
    rejection bar's actual extreme, `zone` puts it under the whole HPCZ. The second
    is the stricter structural invalidation and does not punish a deep rejection
    wick with an oversized stop.
    """
    b = frame(sym, tf, swing_n, min_swing, _market=C.MARKET)
    d, st = b["d"], b["st"]
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    atr, e25, e50 = d.atr.values, d.ema25.values, d.ema50.values
    dates = d.date.values
    midt, hight = d.mid_trend.values, d.high_trend.values
    highn = d.high_nlab.values
    N = len(d)
    pip = C.pip_size(sym)

    rows = []
    piv_i, piv_k, piv_p, piv_c = st["piv_i"], st["piv_k"], st["piv_p"], st["piv_c"]

    for k in range(len(piv_i)):
        i0 = int(piv_c[k])                      # bar the pivot became knowable
        if i0 >= N - 5 or not np.isfinite(atr[i0]) or atr[i0] <= 0:
            continue
        dr = int(st["trend"][i0])
        if dr == 0:
            continue
        # GATE 1 + C1: the pullback we plan for starts at the impulse extreme, so the
        # freshly confirmed pivot must be a high in an uptrend (or a low in a downtrend)
        if piv_k[k] != dr or int(st["last_kind"][i0]) != dr:
            continue

        ext = float(piv_p[k])                                    # impulse extreme  (b)
        base = float(st["lastL_p"][i0] if dr > 0 else st["lastH_p"][i0])   # leg origin (a)
        if not np.isfinite(base) or abs(ext - base) < 1e-9:
            continue
        leg = abs(ext - base)
        if leg < 1.5 * atr[i0]:                 # a leg smaller than the noise is not an impulse
            continue

        # ---------------- GATE 4: build the HPCZ around the strongest anchor
        w = band_atr * atr[i0]
        fibs = S.fib_levels(base, ext) if dr > 0 else S.fib_levels(base, ext)
        anchors = [("fib", x) for x in fibs] + [("ema", e25[i0]), ("ema", e50[i0])]
        best = None
        for _, x in anchors:
            if not np.isfinite(x):
                continue
            lo, hi = x - w, x + w
            # the zone must sit inside the retracement corridor, on the correct side
            r = (ext - x) / (ext - base) if dr > 0 else (x - ext) / (base - ext)
            if not (min_retrace <= r <= max_retrace):
                continue
            f_struct = True                       # C4: inside the corridor => a valid HL/LH
            f_ema = (lo <= e25[i0] <= hi) or (lo <= e50[i0] <= hi)
            f_fib = any(lo <= q <= hi for q in fibs)
            f_sr = S.sr_touches(st, i0, lo, hi, sr_lookback, sr_touches)
            f_tl = S.trendline(st, i0, dr, lo, hi, tl_touches, 0.35, atr[i0])
            # continuation shape: the pullback's ranges are contracting into the zone
            a1 = np.nanmean(h[max(0, i0 - 5):i0 + 1] - l[max(0, i0 - 5):i0 + 1])
            a2 = np.nanmean(h[max(0, i0 - 11):i0 - 5] - l[max(0, i0 - 11):i0 - 5])
            f_pat = bool(a2 > 0 and a1 / a2 < 0.8)
            n_conf = sum((f_struct, f_ema, f_fib, f_sr, f_tl, f_pat))
            key = (n_conf, -abs(r - 0.5))
            if best is None or key > best[0]:
                best = (key, dict(lo=lo, hi=hi, mid=x, r=r, n=n_conf, ema=f_ema,
                                  fib=f_fib, sr=f_sr, tl=f_tl, pat=f_pat))
        if best is None:
            continue
        z = best[1]

        # GATE 3 / B3 in its most literal form: E1 (RR>=2) and E2 (SL>=2*ATR) can
        # only both hold if the impulse leg is long enough to pay for them, so the
        # leg's size in ATR is the real gatekeeper and belongs in the sweep.
        base_row = dict(
            symbol=sym, dr=dr, plan_time=dates[i0], plan_i=i0,
            leg_atr=float(leg / atr[i0]),
            nlab=int(st["nlab"][i0]), legs=int(st["legs"][i0]),
            ext_atr=float(abs(c[i0] - e50[i0]) / atr[i0]),
            # B1 reads the TOP frame (日線 for a 1H stack), B2 the middle one (4H)
            d_ok=bool(hight[i0] != 0 and highn[i0] >= 2),
            h4_align=bool(midt[i0] == dr), dd_align=bool(hight[i0] == dr),
            n_conf=z["n"], has_ema=z["ema"], has_fib=z["fib"], has_sr=z["sr"],
            has_tl=z["tl"], has_pat=z["pat"], retrace=float(z["r"]),
            zone_lo=z["lo"], zone_hi=z["hi"], target=ext, stage=STAGE["zone"],
        )

        # C2: the zone must still be ahead of price when the plan is written
        if (dr > 0 and z["hi"] >= l[i0]) or (dr < 0 and z["lo"] <= h[i0]):
            rows.append(base_row)
            continue
        base_row["stage"] = STAGE["untouched"]

        # ---------------- GATE 6: wait for price, then for the entry trigger
        near = z["hi"] if dr > 0 else z["lo"]        # edge price reaches first
        far = z["lo"] if dr > 0 else z["hi"]
        touch = -1
        extreme = np.nan
        done = False
        for j in range(i0 + 1, min(N, i0 + PLAN_TTL)):
            if int(st["trend"][j]) != dr:            # structure moved on — plan is void
                break
            if touch < 0:
                if (dr > 0 and l[j] <= near) or (dr < 0 and h[j] >= near):
                    touch = j
                    base_row["stage"] = STAGE["touched"]
                    extreme = l[j] if dr > 0 else h[j]
                else:
                    continue
            else:
                extreme = min(extreme, l[j]) if dr > 0 else max(extreme, h[j])
            if j - touch > max_wait:
                break
            f_w, f_e, f_p = _confirm(o, h, l, c, j, dr, wick_frac)

            if entry_mode.startswith("limit"):
                # resting order: fills on the touch bar, at the edge or better if gapped
                entry = min(near, o[j]) if dr > 0 else max(near, o[j])
                sl = far - dr * sl_buffer * atr[i0]
                sim_start = j                       # the fill bar can stop us out itself
            else:
                # D3: driven straight through the zone with momentum -> setup is dead
                if (dr > 0 and c[j] < z["lo"] - 0.5 * atr[j]) or \
                   (dr < 0 and c[j] > z["hi"] + 0.5 * atr[j]):
                    break
                if not (f_w or f_e or f_p):         # D2: no rejection, keep waiting
                    continue
                entry = c[j]
                anchor = extreme if sl_mode == "wick" else \
                    (min(extreme, far) if dr > 0 else max(extreme, far))
                sl = anchor - dr * sl_buffer * atr[j]
                sim_start = None

            risk = abs(entry - sl)
            tp = ext
            if tp_mode == "ext":
                # 1.272 extension of the impulse: the manual's target, pushed one
                # fib beyond, for markets where the prior extreme is too close
                tp = ext + dr * 0.272 * abs(ext - base)
            reward = (tp - entry) * dr
            if not np.isfinite(risk) or risk / pip < 2.0 or reward <= 0:
                break
            # E2 must be checkable at the moment the order is decided. A resting limit
            # is decided at the plan, so it uses the plan bar's ATR; a market order is
            # decided on the confirmation close, so that bar's ATR is already known.
            atr_dec = atr[i0] if entry_mode.startswith("limit") else atr[j]
            bars_ = [htf_barrier(b, tag, dates[j], entry, dr, risk)
                     for tag in ("mid", "high")]
            barrier = min(bars_) if dr > 0 else max(bars_)
            room = (barrier - entry) * dr / risk if np.isfinite(barrier) else 99.0

            if entry_mode == "limit_confirm" and not (f_w or f_e or f_p):
                # D4 without the look-ahead: the limit already filled intrabar, so the
                # only honest way to demand a closed rejection bar is to scratch the
                # position at that close when none printed. A stop breached inside the
                # same bar still counts as a stop — we cannot know it came after.
                if (dr > 0 and l[j] <= sl) or (dr < 0 and h[j] >= sl):
                    xi, xp, why = j, sl, "sl"
                else:
                    xi, xp, why = j, c[j], "scratch"
            else:
                xi, xp, why = _simulate(o, h, l, c, j, entry, sl, tp, dr, sim_start,
                                        atr, tp_mode, trail_atr)
            r_gross = (xp - entry) * dr / risk
            # equities pay a share of notional, FX a fixed spread — cost_px knows
            cost_r = C.cost_px(sym, entry) / risk

            base_row.update(
                stage=STAGE["entered"], entry_time=dates[j], entry_i=j, entry=entry,
                sl=sl, tp=tp, risk_px=risk, risk_pips=risk / pip,
                sl_atr=risk / atr_dec, rr=reward / risk, room_R=room,
                c_wick=f_w, c_engulf=f_e, c_pin=f_p,
                exit_time=dates[xi], exit_i=xi, exit=xp, exit_why=why,
                bars_held=xi - j, wait_bars=j - touch,
                r_gross=r_gross, cost_r=cost_r, r_net=r_gross - cost_r,
            )
            rows.append(base_row)
            done = True
            break
        if not done:
            rows.append(base_row)

    df = pd.DataFrame(rows)
    if len(df):
        df["tf"] = tf
        df["entry_mode"] = entry_mode
        df["sl_mode"] = sl_mode
        df["tp_mode"] = tp_mode
        df["swing_n"] = swing_n
        df["min_swing"] = min_swing
        df["band_atr"] = band_atr
    return df


def _safe(sym, **kw):
    # a few names have no intraday file (IPO'd after the 730-day window, or the
    # download dropped them); skipping beats aborting a 12-minute sweep
    try:
        return scan(sym, **kw)
    except FileNotFoundError:
        return pd.DataFrame()


def scan_all(symbols=None, **kw):
    out = [_safe(s, **kw) for s in (symbols or C.SYMBOLS)]
    out = [d for d in out if len(d)]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---------------------------------------------------------------- the manual's mask
def preconditions(df, p):
    """A1–E4 as one boolean mask over a candidate table.

    Every flag maps to a numbered precondition in §07 of the manual; the names are
    kept so a rejected trade can be explained by ID rather than by vibes.
    """
    m = df.stage == STAGE["entered"]
    m &= df.nlab >= p["min_labels"]                                   # A1 + A2
    m &= df.legs <= p["max_legs"]                                     # A3
    m &= df.ext_atr <= p["max_ext_atr"]                               # GATE 2 乖離
    if p.get("mtf_strict", True):
        m &= df.d_ok                                                  # B1
        m &= df.h4_align                                              # B2
    m &= df.room_R >= p["min_rr"]                                     # B3
    m &= df.n_conf >= p["min_conf"]                                   # C2
    if p.get("require_ema", True):
        m &= df.has_ema                                               # C3
    m &= df.retrace >= p["min_retrace"]                               # C1 + C4
    m &= df.rr >= p["min_rr"]                                         # E1
    m &= df.sl_atr >= p["sl_atr_mult"]                                # E2
    conf = p.get("confirm", "any")                                    # D2
    if conf == "wick":
        m &= df.c_wick
    elif conf == "engulf":
        m &= df.c_engulf
    elif conf == "pin":
        m &= df.c_pin
    return m.fillna(False)
