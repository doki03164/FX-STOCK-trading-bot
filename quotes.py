"""Live re-pricing of an existing plan book, without re-running the gates.

The split that makes a 10-second refresh possible:

  * The PLAN — structure, confluence, zone, SL, TP, size — is derived from closed
    bars. It cannot change until the next bar closes. Re-deriving it every ten
    seconds would be 545 symbols of downloads for an answer that did not move.
  * The STATE — has price reached the zone yet, how far away is it, has the setup
    been invalidated — changes tick by tick, and depends on one number per symbol.

So this module fetches only the last price, only for symbols that currently carry
a live plan (typically 20-50, not 545), and re-evaluates the second list against
the first. One batched request per refresh.
"""
import time
import warnings

import config as C

warnings.filterwarnings("ignore")

# keyed by the symbol set — a shared cache would let the FX poll starve the
# US one, since the two books never overlap
_CACHE = {}
MIN_INTERVAL = 8.0          # floor between upstream hits, whatever the browsers ask


def latest(symbols):
    """Last traded price per symbol, batched, with a short server-side cache.

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

    import yfinance as yf
    px = {}
    syms = list(symbols)
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
    if px:
        _CACHE[key] = (now, px)
        if len(_CACHE) > 8:                       # only a handful of books exist
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:-8]:
                _CACHE.pop(k, None)
    return px, True


def restate(plan, price):
    """Recompute one plan's live state from a fresh price. Pure, no I/O.

    Mirrors the state machine in bot.scan_live exactly — the gates were already
    decided on the last closed bar, so only the position of price relative to the
    zone is in question here.
    """
    dr = plan["dr"]
    near = plan["entry"]
    lo, hi = plan["zone_lo"], plan["zone_hi"]
    inval = plan["invalidate"]
    failed = plan.get("failed", [])

    in_zone = (price <= hi) if dr > 0 else (price >= lo)
    dead = (price < inval) if dr > 0 else (price > inval)
    gap = (price - near) * dr           # >0: price still has to come back to us

    if dead:
        state = "invalid"
    elif failed:
        state = "blocked"
    elif in_zone:
        state = "ready"
    else:
        state = "armed"

    unit = 1.0 if not C.is_fx(plan["sym"]) else C.pip_size(plan["sym"])
    return {
        "state": state,
        "price": round(price, 2 if not C.is_fx(plan["sym"]) else 5),
        "in_zone": bool(in_zone),
        "gap_pips": round(gap / unit, 2),
        "gap_pct": round(abs(gap) / price * 100, 2) if price else 0.0,
    }


def repriced(plans, market):
    """Apply fresh prices to a plan book; returns (updates, changed_keys, fetched)."""
    C.set_market(market)
    live = [p for p in plans if p["state"] in ("ready", "armed")]
    px, fetched = latest([p["sym"] for p in live])
    out, changed = {}, []
    for p in plans:
        if p["sym"] not in px:
            continue
        u = restate(p, px[p["sym"]])
        key = f"{p['sym']}|{p['tf']}"
        out[key] = u
        if u["state"] != p["state"]:
            changed.append({"key": key, "name": p["name"], "tf": p["tf"],
                            "from": p["state"], "to": u["state"]})
    return out, changed, fetched
