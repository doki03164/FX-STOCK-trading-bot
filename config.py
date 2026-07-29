"""Universe, cost model and strategy defaults for the 交易操作手冊 FX bot.

Everything the rest of the package treats as "a fact about the market" lives here,
so a broker change is a one-file edit rather than a grep.
"""
import os

import markets as M

HERE = os.path.dirname(os.path.abspath(__file__))

# Which market the whole process is looking at. Every script accepts --market and
# calls set_market() before touching anything else; the engine itself never asks.
MARKET = "fx"
DATA = os.path.join(HERE, "data", "fx")


def set_market(m):
    """Rebind the module-level universe, data directory and cost model."""
    global MARKET, DATA, UNIVERSE, SYMBOLS
    if m not in M.MARKETS:
        raise ValueError(f"unknown market {m!r}; expected one of {list(M.MARKETS)}")
    MARKET = m
    DATA = os.path.join(HERE, "data", m)
    UNIVERSE = M.FX_UNIVERSE if m == "fx" else M.us_universe()
    SYMBOLS = list(UNIVERSE)
    return m


def suffix(name, ext):
    """Per-market artefact name, e.g. sweep -> sweep_us.csv."""
    return f"{name}_{MARKET}.{ext}"


def art(name, ext):
    return os.path.join(HERE, suffix(name, ext))

# ---------------------------------------------------------------- universe
# Yahoo ticker -> display name. Forex only, per the manual's watchlist shape
# (majors + crosses). Kept at 28 pairs so the sweep still finishes in minutes.
UNIVERSE = {
    # --- USD majors ---
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "USDCHF=X": "USD/CHF", "AUDUSD=X": "AUD/USD", "NZDUSD=X": "NZD/USD",
    "USDCAD=X": "USD/CAD",
    # --- JPY crosses ---
    "EURJPY=X": "EUR/JPY", "GBPJPY=X": "GBP/JPY", "AUDJPY=X": "AUD/JPY",
    "CADJPY=X": "CAD/JPY", "CHFJPY=X": "CHF/JPY", "NZDJPY=X": "NZD/JPY",
    # --- EUR crosses ---
    "EURGBP=X": "EUR/GBP", "EURAUD=X": "EUR/AUD", "EURCAD=X": "EUR/CAD",
    "EURCHF=X": "EUR/CHF", "EURNZD=X": "EUR/NZD",
    # --- GBP crosses ---
    "GBPAUD=X": "GBP/AUD", "GBPCAD=X": "GBP/CAD", "GBPCHF=X": "GBP/CHF",
    "GBPNZD=X": "GBP/NZD",
    # --- commodity-bloc crosses ---
    "AUDCAD=X": "AUD/CAD", "AUDCHF=X": "AUD/CHF", "AUDNZD=X": "AUD/NZD",
    "NZDCAD=X": "NZD/CAD", "NZDCHF=X": "NZD/CHF", "CADCHF=X": "CAD/CHF",
    # --- beyond G10 ---
    # The 28 above are every G10 combination there is (8 currencies -> C(8,2) = 28),
    # so more data has to come from outside the bloc. These are the liquid Scandi and
    # EM crosses; pegged or managed rates (HKD, DKK, CNY) are deliberately excluded —
    # a currency that does not trend cannot test a trend-following rulebook.
    "USDSEK=X": "USD/SEK", "USDNOK=X": "USD/NOK", "USDSGD=X": "USD/SGD",
    "USDMXN=X": "USD/MXN", "USDZAR=X": "USD/ZAR", "USDTRY=X": "USD/TRY",
    "USDPLN=X": "USD/PLN", "USDHUF=X": "USD/HUF", "USDCZK=X": "USD/CZK",
    "USDILS=X": "USD/ILS",
    "EURSEK=X": "EUR/SEK", "EURNOK=X": "EUR/NOK", "EURPLN=X": "EUR/PLN",
    "EURHUF=X": "EUR/HUF", "EURTRY=X": "EUR/TRY", "EURCZK=X": "EUR/CZK",
    "GBPSEK=X": "GBP/SEK", "GBPNOK=X": "GBP/NOK",
}

# Anything outside this bloc pays a much wider spread; see SPREAD_PIPS.
G10 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}

SYMBOLS = list(UNIVERSE)

# ---------------------------------------------------------------- timeframe stacks
# The manual's structure is 日線 = 能不能做 / 4H = 有沒有空間 / 1H = 怎麼做 — a ladder,
# not three fixed charts. Slide the whole ladder up and the same rules apply to a 4H or
# daily execution chart. That matters because dealing cost is a fixed number of pips:
# ~0.17R of every 1H trade, but only ~0.02R of a daily one.
TF_STACK = {
    "1h": {"mid": "4h", "high": "1d"},
    "4h": {"mid": "1d", "high": "1w"},
    "1d": {"mid": "1w", "high": "1mo"},
}
EXEC_TFS = list(TF_STACK)
ALL_TFS = ["1h", "4h", "1d", "1w", "1mo"]
TF_LABEL = {"1h": "1 小時", "4h": "4 小時", "1d": "日線", "1w": "週線", "1mo": "月線"}


def pair(sym):
    """'EURUSD=X' -> ('EUR', 'USD')."""
    s = sym.replace("=X", "")
    return s[:3], s[3:]


def is_fx(sym):
    return sym.endswith("=X")


def pip_size(sym):
    """FX: JPY and HUF quote to two decimals, others four. Equities: one cent."""
    if not is_fx(sym):
        return 0.01
    return 0.01 if pair(sym)[1] in ("JPY", "HUF") else 0.0001


def cost_px(sym, price=None):
    """Round-trip dealing cost in PRICE units — the one number the engine needs.

    FX quotes a fixed spread in pips regardless of level; equities quote it as a
    fraction of notional, so a $500 share costs ten times a $50 one to trade.
    """
    if is_fx(sym):
        return cost_pips(sym) * pip_size(sym)
    return float(price or 100.0) * M.US_COST_BPS / 1e4


# Round-turn dealing cost in pips: raw spread + commission, sized for a prop-firm
# account during liquid hours. Deliberately pessimistic — an over-optimistic cost
# model is the single easiest way to make a losing 1H strategy look profitable.
SPREAD_PIPS = {
    "EURUSD=X": 0.8, "GBPUSD=X": 1.0, "USDJPY=X": 0.9, "USDCHF=X": 1.2,
    "AUDUSD=X": 1.0, "NZDUSD=X": 1.4, "USDCAD=X": 1.3,
    "EURJPY=X": 1.4, "GBPJPY=X": 2.2, "AUDJPY=X": 1.6, "CADJPY=X": 1.8,
    "CHFJPY=X": 2.2, "NZDJPY=X": 2.0,
    "EURGBP=X": 1.2, "EURAUD=X": 2.0, "EURCAD=X": 2.2, "EURCHF=X": 1.5,
    "EURNZD=X": 3.0,
    "GBPAUD=X": 3.0, "GBPCAD=X": 3.2, "GBPCHF=X": 2.6, "GBPNZD=X": 4.0,
    "AUDCAD=X": 1.8, "AUDCHF=X": 1.8, "AUDNZD=X": 2.2,
    "NZDCAD=X": 2.4, "NZDCHF=X": 2.6, "CADCHF=X": 2.2,
    # Beyond G10 the spread is an order of magnitude wider, and that is the whole
    # point of including them honestly: cheap-looking edges on exotics usually die
    # here. Figures are typical prop-firm quotes during the London session.
    "USDSEK=X": 22.0, "USDNOK=X": 25.0, "USDSGD=X": 3.0,
    "USDMXN=X": 35.0, "USDZAR=X": 60.0, "USDTRY=X": 90.0,
    "USDPLN=X": 25.0, "USDHUF=X": 6.0, "USDCZK=X": 30.0, "USDILS=X": 30.0,
    "EURSEK=X": 25.0, "EURNOK=X": 28.0, "EURPLN=X": 28.0,
    "EURHUF=X": 7.0, "EURTRY=X": 110.0, "EURCZK=X": 30.0,
    "GBPSEK=X": 40.0, "GBPNOK=X": 45.0,
}
COMMISSION_PIPS = 0.7   # ~$7 per standard lot round turn on a raw account
SLIPPAGE_PIPS = 0.3     # market order on the confirmation close


def cost_pips(sym):
    if not is_fx(sym):
        return 0.0
    return SPREAD_PIPS.get(sym, 2.0) + COMMISSION_PIPS + SLIPPAGE_PIPS


# ---------------------------------------------------------------- strategy defaults
# These are the manual's own numbers. The sweep exists precisely because the manual
# says the thresholds "必須由你自己的回測數據決定".
DEFAULTS = {
    "tf": "1h",            # 執行週期；context 由 TF_STACK 決定
    # GATE 1 — structure
    "swing_n": 5,          # 擺動點：左右各 5 根
    "min_labels": 3,       # 至少 3 個同向標籤
    # GATE 2 — maturity
    "max_legs": 5,         # 早期/中期 = 1-5 推動浪；6 以上為成熟
    "max_ext_atr": 4.0,    # 乖離極端：距 50 EMA 的 ATR 倍數上限
    # GATE 3 — MTF
    "mtf_strict": True,    # 4H 結構方向必須與 1H 相同、日線階段須明確
    # GATE 4 — HPCZ
    "band_atr": 0.5,       # 共振帶的半寬（ATR 倍數）
    "min_conf": 3,         # 至少 3 個共振因素
    "require_ema": True,   # C3：共振之一必須是 25 或 50 EMA
    "sr_touches": 2,       # 水平位至少反應過 2 次
    "tl_touches": 3,       # 趨勢線至少 3 個觸點
    "min_retrace": 0.236,  # 進入回撤末端才算數
    # GATE 5 — risk
    "min_rr": 2.0,         # 盈虧比 ≥ 1:2
    "sl_atr_mult": 2.0,    # SL 距離 ≥ 2 × ATR(14)
    "sl_buffer_atr": 0.2,  # SL = 結構點 ± 0.2 × ATR
    "risk_pct": 0.01,      # 單筆風險 ≤ 1%
    # GATE 6 — confirmation
    "confirm": "any",      # any | wick | engulf | pin
    "wick_frac": 0.40,     # 影線 ≥ K 棒全幅的 40%
    "max_wait_bars": 24,   # 價格進區後等待確認的上限，超過視為計畫作廢
}

# ---------------------------------------------------------------- account rules
ACCOUNT = {
    "capital": 100_000.0,
    "risk_pct": 0.01,        # E3
    "max_trades_day": 2,     # 當日已開 2 筆 → 停
    "max_open": 4,           # 保證金使用率的粗代理
    "daily_stop": 0.02,      # 單日虧損 2% → 當日停止
    "weekly_stop": 0.05,     # 單週虧損 5% → 當週停止
    "loss_streak": 3,        # 連續 3 筆虧損 → 暫停 1 日
    "max_ccy_exposure": 1,   # 相關性檢查：任一貨幣的淨曝險不得超過 1 筆
}
