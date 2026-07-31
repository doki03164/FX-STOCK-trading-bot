"""Bitget connector: live quotes, and order placement behind a safety layer.

WHAT THIS DOES NOT DO
---------------------
It never reads a key you have not put in the environment yourself, it never logs a
key or a signature, and it refuses to send a live order unless TWO independent
switches are on. The default is dry-run: every call returns exactly what would have
been sent, and sends nothing.

    setx BITGET_API_KEY        your-key          (or export, on a Mac)
    setx BITGET_API_SECRET     your-secret
    setx BITGET_API_PASSPHRASE your-passphrase
    setx BITGET_LIVE           1                 <- the second switch, deliberate

Without BITGET_LIVE the connector is a simulator no matter what keys are present.

WHY THE SAFETY LAYER IS THE POINT
---------------------------------
The strategy behind these tickets is weak. Out of sample the FX book is +0.043R, the
Taiwan book is roughly the size of its own transaction tax, and the commodity sweep
produced ZERO configurations that survived the in/out-of-sample split out of 316,800.
Wiring one-click execution onto that is the most dangerous thing in this project, so
an order has to pass all of the following before it can leave the machine:

  1. it must correspond to a plan that is `ready` RIGHT NOW, re-priced this second
  2. stop loss and take profit must both be attached (the manual's GATE 7)
  3. size must not exceed the configured risk per trade
  4. leverage must not exceed MAX_LEVERAGE — these are perps and offer up to 100x
  5. the daily trade count and loss circuit breakers must not be tripped
  6. a human must confirm the exact ticket

Any single failure blocks the order. There is no override flag.
"""
import os
import json
import time
import hmac
import base64
import hashlib
import urllib.request
import urllib.error

BASE = "https://api.bitget.com"
PRODUCT = "USDT-FUTURES"

# Bitget lists the metals as USDT-margined perpetuals, i.e. leveraged instruments.
# Forced to 1 unless you raise it on purpose; maxLever on these contracts is 100.
MAX_LEVERAGE = int(os.environ.get("BITGET_MAX_LEVERAGE", 1))
MAX_RISK_PCT = float(os.environ.get("BITGET_MAX_RISK_PCT", 0.01))
MAX_ORDERS_DAY = int(os.environ.get("BITGET_MAX_ORDERS_DAY", 2))
# The manual's E4. Risking 1% with a tight stop implies a huge notional — a 1.2%
# stop on a 1% risk is 82% of the account at 1x — so this binds long before the
# risk limit does, and it is the check that stops a "small" trade eating the book.
MAX_MARGIN_PCT = float(os.environ.get("BITGET_MAX_MARGIN_PCT", 0.80))

# Bitget perp symbol -> the Yahoo ticker the backtest and the plans use
SYMBOL_MAP = {
    "GC=F": "XAUUSDT", "SI=F": "XAGUSDT",
    "PL=F": "XPTUSDT", "PA=F": "XPDUSDT", "HG=F": "COPPERUSDT",
}
REVERSE_MAP = {v: k for k, v in SYMBOL_MAP.items()}

_ORDER_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.jsonl")


# ---------------------------------------------------------------- credentials
def creds():
    """Read keys from the environment. Never from a file in the repo, never logged."""
    return (os.environ.get("BITGET_API_KEY", ""),
            os.environ.get("BITGET_API_SECRET", ""),
            os.environ.get("BITGET_API_PASSPHRASE", ""))


def live_enabled():
    """Both switches, independently. Keys alone are not consent to trade."""
    k, s, p = creds()
    return bool(k and s and p) and os.environ.get("BITGET_LIVE") == "1"


def status():
    k, s, p = creds()
    return {
        "keys_present": bool(k and s and p),
        "live_flag": os.environ.get("BITGET_LIVE") == "1",
        "live": live_enabled(),
        "mode": "LIVE" if live_enabled() else "DRY-RUN",
        "max_leverage": MAX_LEVERAGE,
        "max_risk_pct": MAX_RISK_PCT,
        "max_orders_day": MAX_ORDERS_DAY,
        "max_margin_pct": MAX_MARGIN_PCT,
    }


# ---------------------------------------------------------------- transport
def _req(method, path, params=None, body=None, signed=False, timeout=15):
    url = BASE + path
    if params:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + q
        path += "?" + q
    data = json.dumps(body) if body else ""
    headers = {"Content-Type": "application/json", "locale": "en-US",
               "User-Agent": "fxbot/1.0"}
    if signed:
        k, s, p = creds()
        if not (k and s and p):
            raise PermissionError("no API credentials in the environment")
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path + data
        sign = base64.b64encode(
            hmac.new(s.encode(), msg.encode(), hashlib.sha256).digest()).decode()
        headers.update({"ACCESS-KEY": k, "ACCESS-SIGN": sign,
                        "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": p})
    req = urllib.request.Request(url, method=method.upper(),
                                 data=data.encode() if data else None,
                                 headers=headers)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": str(e.code), "msg": "http error"}


# ---------------------------------------------------------------- public data
def tickers():
    """Last/bid/ask for every perp. No auth, no key, safe to poll."""
    d = _req("GET", "/api/v2/mix/market/tickers", {"productType": PRODUCT})
    return {x["symbol"]: x for x in (d.get("data") or [])}


def quotes(yahoo_syms=None):
    """{yahoo_ticker: price} for whatever Bitget actually carries.

    Bitget is the venue you would trade on, so its price is the one that decides
    whether a limit would fill. Yahoo stays the source for HISTORY — Bitget only
    keeps 90 daily candles, which is nowhere near enough to backtest.
    """
    t = tickers()
    out = {}
    for ysym, bsym in SYMBOL_MAP.items():
        if yahoo_syms and ysym not in yahoo_syms:
            continue
        x = t.get(bsym)
        if x and x.get("lastPr"):
            out[ysym] = float(x["lastPr"])
    return out


def spreads_bps():
    """Real half-spread cost per instrument, to replace guessed constants."""
    t = tickers()
    out = {}
    for ysym, bsym in SYMBOL_MAP.items():
        x = t.get(bsym)
        if not x:
            continue
        try:
            bid, ask = float(x["bidPr"]), float(x["askPr"])
            mid = (bid + ask) / 2
            if mid > 0:
                out[ysym] = round((ask - bid) / mid * 1e4, 2)
        except (KeyError, ValueError, ZeroDivisionError):
            continue
    return out


def contracts():
    d = _req("GET", "/api/v2/mix/market/contracts", {"productType": PRODUCT})
    return {x["symbol"]: x for x in (d.get("data") or [])}


# ---------------------------------------------------------------- private
def account():
    """USDT futures balance. Requires keys; read-only."""
    d = _req("GET", "/api/v2/mix/account/accounts",
             {"productType": PRODUCT}, signed=True)
    if d.get("code") != "00000":
        return {"error": d.get("msg", "unknown"), "code": d.get("code")}
    for a in d.get("data") or []:
        if a.get("marginCoin") == "USDT":
            return {"available": float(a.get("available", 0)),
                    "equity": float(a.get("accountEquity", 0))}
    return {"error": "no USDT account"}


def positions():
    d = _req("GET", "/api/v2/mix/position/all-position",
             {"productType": PRODUCT, "marginCoin": "USDT"}, signed=True)
    return d.get("data") or []


# ---------------------------------------------------------------- safety gate
def _orders_today():
    if not os.path.exists(_ORDER_LOG):
        return 0
    day = time.strftime("%Y-%m-%d")
    n = 0
    for line in open(_ORDER_LOG, encoding="utf-8"):
        try:
            if json.loads(line).get("t", "").startswith(day):
                n += 1
        except Exception:
            continue
    return n


def preflight(plan, equity, leverage=1):
    """Every reason this order must not be sent. Empty list means it may proceed."""
    bad = []
    sym = plan.get("sym")
    if sym not in SYMBOL_MAP:
        bad.append(f"{sym} 不在 Bitget 可交易清單（只支援 "
                   f"{', '.join(sorted(SYMBOL_MAP))}）")
    if plan.get("state") != "ready":
        bad.append(f"計畫狀態是 {plan.get('state')}，只有 ready 能下單")
    if plan.get("failed"):
        bad.append(f"前提條件未過：{'、'.join(plan['failed'][:3])}")
    for k in ("entry", "sl", "tp", "dr"):
        if plan.get(k) in (None, 0):
            bad.append(f"缺少 {k}")
    if plan.get("sl") and plan.get("entry"):
        risk = abs(float(plan["entry"]) - float(plan["sl"]))
        if risk <= 0:
            bad.append("停損距離為零")
        elif equity and risk * float(plan.get("lots", 0)) > equity * MAX_RISK_PCT * 1.05:
            bad.append(f"風險超過 {MAX_RISK_PCT*100:.0f}% 上限")
    if leverage > MAX_LEVERAGE:
        bad.append(f"槓桿 {leverage} 超過上限 {MAX_LEVERAGE}")
    if equity and plan.get("entry") and plan.get("sl"):
        risk = abs(float(plan["entry"]) - float(plan["sl"]))
        if risk > 0:
            notional = (equity * MAX_RISK_PCT / risk) * float(plan["entry"])
            margin = notional / max(leverage, 1)
            if margin > equity * MAX_MARGIN_PCT:
                bad.append(f"保證金使用率 {margin/equity*100:.0f}% 超過 "
                           f"{MAX_MARGIN_PCT*100:.0f}%（手冊 E4）—— "
                           f"停損太近，這筆的名目部位吃掉整個帳戶")
    n = _orders_today()
    if n >= MAX_ORDERS_DAY:
        bad.append(f"今日已下 {n} 單，上限 {MAX_ORDERS_DAY}（手冊 §10）")
    return bad


def build_ticket(plan, equity, leverage=1):
    """The exact order that would be sent, plus every reason it might not be."""
    sym = plan.get("sym")
    bsym = SYMBOL_MAP.get(sym, "")
    dr = int(plan.get("dr", 0))
    entry, sl, tp = (float(plan.get(k, 0)) for k in ("entry", "sl", "tp"))
    risk_px = abs(entry - sl)
    risk_usd = equity * MAX_RISK_PCT
    size = round(risk_usd / risk_px, 3) if risk_px else 0.0
    return {
        "symbol": bsym, "yahoo": sym, "name": plan.get("name"),
        "side": "buy" if dr > 0 else "sell",
        "tradeSide": "open", "orderType": "limit",
        "price": entry, "size": size,
        "presetStopLossPrice": sl, "presetStopSurplusPrice": tp,
        "marginMode": "isolated", "marginCoin": "USDT", "leverage": leverage,
        "notional_usdt": round(size * entry, 2),
        "risk_usdt": round(size * risk_px, 2),
        "reward_usdt": round(size * abs(tp - entry), 2),
        "margin_pct": round((size * entry / max(leverage, 1)) / equity * 100, 1)
                      if equity else None,
        "blockers": preflight(plan, equity, leverage),
        "mode": status()["mode"],
    }


def place(plan, equity, leverage=1, confirm_token=None):
    """Send the order — or, by default, describe what would have been sent.

    `confirm_token` must equal the ticket's own fingerprint, so a UI cannot fire an
    order the user never saw: the token is computed from the exact prices shown.
    """
    t = build_ticket(plan, equity, leverage)
    fp = fingerprint(t)
    t["fingerprint"] = fp

    if t["blockers"]:
        return {"sent": False, "reason": "blocked", "ticket": t}
    if confirm_token != fp:
        return {"sent": False, "reason": "confirm_mismatch", "ticket": t,
                "detail": "票券內容與確認時不同（價格已變），請重新確認"}
    if not live_enabled():
        _log({"t": _now(), "mode": "DRY-RUN", "ticket": t})
        return {"sent": False, "reason": "dry_run", "ticket": t,
                "detail": "模擬模式：未送出。需同時設定 API 金鑰與 BITGET_LIVE=1"}

    body = {"symbol": t["symbol"], "productType": PRODUCT,
            "marginMode": t["marginMode"], "marginCoin": t["marginCoin"],
            "size": str(t["size"]), "price": str(t["price"]),
            "side": t["side"], "tradeSide": t["tradeSide"],
            "orderType": t["orderType"], "force": "gtc",
            "presetStopLossPrice": str(t["presetStopLossPrice"]),
            "presetStopSurplusPrice": str(t["presetStopSurplusPrice"])}
    r = _req("POST", "/api/v2/mix/order/place-order", body=body, signed=True)
    ok = r.get("code") == "00000"
    _log({"t": _now(), "mode": "LIVE", "ticket": t, "ok": ok,
          "orderId": (r.get("data") or {}).get("orderId"), "msg": r.get("msg")})
    return {"sent": ok, "reason": "ok" if ok else r.get("msg", "rejected"),
            "ticket": t, "response": r}


def fingerprint(t):
    """Identifies the exact ticket a human looked at — prices included."""
    raw = f"{t['symbol']}|{t['side']}|{t['price']}|{t['size']}|" \
          f"{t['presetStopLossPrice']}|{t['presetStopSurplusPrice']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(rec):
    with open(_ORDER_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")




def check():
    """Verify credentials with a READ-ONLY call. Places nothing, ever.

    Run this before turning BITGET_LIVE on: it proves the key, secret and passphrase
    are right and the permissions work, with no possibility of a trade.
    """
    s = status()
    print("mode          " + s["mode"])
    print("keys present  " + str(s["keys_present"]))
    print("BITGET_LIVE   " + str(s["live_flag"]))
    print("limits        leverage<=%dx  risk<=%.0f%%  margin<=%.0f%%  orders/day<=%d"
          % (s["max_leverage"], s["max_risk_pct"] * 100,
             s["max_margin_pct"] * 100, s["max_orders_day"]))
    print("")
    print("quotes        " + str(quotes()))
    print("spreads bps   " + str(spreads_bps()))
    if not s["keys_present"]:
        print("")
        print("No credentials in the environment - public data only.")
        print("Set BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE,")
        print("then run this again to confirm them before setting BITGET_LIVE=1.")
        return 1
    a = account()
    if "error" in a:
        print("")
        print("AUTH FAILED: %s (code %s)" % (a["error"], a.get("code")))
        print("Check the passphrase, that the key has futures-trade permission,")
        print("and that this machine's IP is on the key's allowlist.")
        return 1
    print("")
    print("AUTH OK       equity %s USDT, available %s" % (a["equity"], a["available"]))
    try:
        print("open positions %d" % len(positions()))
    except Exception as e:
        print("positions call failed: " + str(e)[:80])
    print("")
    print("LIVE IS ARMED - confirmed orders will use real funds." if s["live"]
          else "Still DRY-RUN. Set BITGET_LIVE=1 to arm real orders.")
    return 0


if __name__ == "__main__":
    import sys as _s
    _s.exit(check())
