"""Live re-pricing of an existing plan book, and the one state machine that decides
whether a plan is still actionable.

Two separate clocks make a fast refresh possible at all:

  * The PLAN — structure, confluence, zone, SL, TP, size — is derived from closed
    bars and cannot change until the next bar closes. Re-deriving it every few
    seconds would be hundreds of downloads for an unchanged answer.
  * The STATE — has price reached the zone, has it fallen through it, has the stop
    already been touched — moves tick by tick and needs one number per symbol.

Because the whole book is too big to re-price at once, symbols are refreshed in
STAGES: anything currently actionable every cycle, everything else on a rotating
slice. `age` on each update says how old that particular price is, so a stale
quote can never masquerade as a live one.
"""
import time
import warnings

import config as C

warnings.filterwarnings("ignore")

# keyed by the symbol set — a shared cache would let the FX poll starve the
# US one, since the two books never overlap
_CACHE = {}
_PX = {}            # market -> {sym: (price, fetched_at)} — survives across cycles
_CURSOR = {}        # market -> rotation offset for the background tier
MIN_INTERVAL = 8.0  # floor between upstream hits, whatever the browsers ask
SLICE = 45          # background symbols re-priced per cycle
STALE_S = 180       # a quote older than this is not trusted for a go/no-go call


def _download(symbols):
    """One batched last-price request. Returns {sym: price}."""
    import yfinance as yf
    px, syms = {}, list(symbols)
    for i in range(0, len(syms), 60):
        chunk = syms[i:i + 60]
        try:
            d = yf.download(chunk, period="1d", interval="1m", progress=False,
                            auto_adjust=False, threads=True, group_by="ticker")
            if d is None or not len(d):
                continue
            for s in chunk:
                try:
                    col = d[s]["Close"] if hasattr(d.columns, "levels") else d["Close"]
                    col = col.dropna()
                    if len(col):
                        px[s] = float(col.iloc[-1])
                except Exception:
                    continue
        except Exception:
            continue
    return px


def latest(symbols):
    """Last price per symbol, batched, with a short server-side cache.

    Several browser tabs polling at once must not multiply the upstream requests,
    hence the cache: the first caller inside the window pays, the rest read it.
    """
    if not symbols:
        return {}, False
    now = time.time()
    key = frozenset(symbols)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < MIN_INTERVAL and hit[1]:
        return hit[1], False
    px = _download(symbols)
    if px:
        _CACHE[key] = (now, px)
        if len(_CACHE) > 8:                       # only a handful of books exist
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:-8]:
                _CACHE.pop(k, None)
    return px, True


# ---------------------------------------------------------------- state machine
def restate(plan, price, age=0.0):
    """Is this plan still actionable at `price`? Pure, no I/O.

    The ordering below is the whole point, and getting it wrong is what let a plan
    read 可下單 after price had already traded through its stop. For a long:

        invalidate  <  SL  <  zone_lo  <  zone_hi = entry

    A resting limit sits at zone_hi, so the moment price is BELOW zone_lo it has
    crossed the entire band — the order filled and the position is underwater. It
    is not a fresh opportunity, whatever `invalidate` says. And `in_zone` needs a
    lower bound: "price <= zone_hi" alone is true all the way down to zero.
    """
    dr = plan["dr"]
    lo, hi = plan["zone_lo"], plan["zone_hi"]
    sl, inval = plan["sl"], plan["invalidate"]
    failed = plan.get("failed", [])
    fx = C.is_fx(plan["sym"])
    unit = C.pip_size(plan["sym"]) if fx else 1.0

    # distance from price to each level, signed so positive always means "in the
    # trade's favour"; lets one expression serve both directions
    def beyond(level):
        return (price - level) * dr < 0        # price has passed the level

    if beyond(sl):
        state, why = "stopped", "價格已觸及停損，這筆計畫結束"
    elif beyond(inval):
        state, why = "invalid", "帶動能貫穿區域，設定作廢"
    elif beyond(lo if dr > 0 else hi):
        state, why = "passed", "價格已跌穿整個進場區，掛單已成交且不利"
    elif (price <= hi if dr > 0 else price >= lo):
        state, why = ("blocked", "條件未通過") if failed else ("ready", "")
    else:
        state, why = ("blocked", "條件未通過") if failed else ("armed", "")

    if age > STALE_S and state == "ready":
        state, why = "stale", f"報價已 {age/60:.0f} 分鐘未更新，不足以判斷進場"

    # A plan is derived from closed bars, so once the NEXT bar has closed the zone,
    # the stop and the target may all have moved and this ticket is a historical
    # artefact. Re-pricing cannot detect that — only a rescan can — so say so.
    nb = plan.get("next_bar")
    if nb and state in ("ready", "armed"):
        try:
            import pandas as _pd
            if _pd.Timestamp(nb) < _pd.Timestamp.utcnow().tz_localize(None):
                state = "expired"
                why = (f"{nb} 起已有新的 K 棒收線，這張計畫是舊的 —— "
                       f"按「重新掃描」重新推導")
        except Exception:
            pass

    gap = (price - plan["entry"]) * dr
    return {
        "state": state, "why": why,
        "price": round(price, 5 if fx else 2),
        "in_zone": bool(state == "ready"),
        "gap_pips": round(gap / unit, 2),
        "gap_pct": round(abs(gap) / price * 100, 2) if price else 0.0,
        "age": int(age),
    }


# ---------------------------------------------------------------- staged refresh
def repriced(plans, market, slice_size=SLICE):
    """Refresh in stages and re-evaluate the whole book.

    Tier 1 — anything a human might act on right now (ready/armed). Re-priced every
             single cycle, because this is where a stale price does damage.
    Tier 2 — the rest, one rotating slice per cycle, so the entire book still comes
             round regularly without ever asking for all of it at once.

    Every plan is then re-stated from the freshest price held for its symbol,
    whichever tier last fetched it, and carries the age of that price.
    """
    C.set_market(market)
    store = _PX.setdefault(market, {})
    now = time.time()

    live = [p["sym"] for p in plans if p["state"] in ("ready", "armed")]
    rest = [p["sym"] for p in plans if p["state"] not in ("ready", "armed")]
    seen = set(live)
    rest = [s for s in dict.fromkeys(rest) if s not in seen]

    cur = _CURSOR.get(market, 0)
    if rest:
        cur %= len(rest)
        batch = rest[cur:cur + slice_size]
        if len(batch) < slice_size:
            batch += rest[:slice_size - len(batch)]
        _CURSOR[market] = (cur + slice_size) % len(rest)
    else:
        batch = []

    px, fetched = latest(list(dict.fromkeys(list(live) + batch)))
    for s, v in px.items():
        store[s] = (v, now)

    out, changed = {}, []
    for p in plans:
        held = store.get(p["sym"])
        if not held:
            continue
        u = restate(p, held[0], now - held[1])
        out[f"{p['sym']}|{p['tf']}"] = u
        if u["state"] != p["state"]:
            changed.append({"key": f"{p['sym']}|{p['tf']}", "name": p["name"],
                            "tf": p["tf"], "from": p["state"], "to": u["state"],
                            "why": u["why"]})
    return out, changed, fetched


def coverage(plans, market):
    """How much of the book is currently backed by a fresh price."""
    store = _PX.get(market, {})
    now = time.time()
    ages = [now - store[p["sym"]][1] for p in plans if p["sym"] in store]
    return {"priced": len(ages), "total": len(plans),
            "fresh": sum(1 for a in ages if a <= STALE_S),
            "oldest": int(max(ages)) if ages else None}
