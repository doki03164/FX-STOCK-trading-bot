"""Market definitions: what to trade, what it costs, and where its data lives.

The strategy engine is asset-agnostic — structure, gates and risk maths never ask
what they are looking at. Only three things actually differ between an FX pair and
a US share, and they all live here:

  * the universe            (28+18 currency pairs vs the S&P 500)
  * the dealing cost        (a fixed pip spread vs a spread in basis points)
  * the calendar            (FX runs 24/5, equities 09:30–16:00 ET on weekdays)

Everything downstream takes `market` as a parameter and stays identical.
"""
import os
import functools

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- FX universe
FX_UNIVERSE = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "USDCHF=X": "USD/CHF", "AUDUSD=X": "AUD/USD", "NZDUSD=X": "NZD/USD",
    "USDCAD=X": "USD/CAD",
    "EURJPY=X": "EUR/JPY", "GBPJPY=X": "GBP/JPY", "AUDJPY=X": "AUD/JPY",
    "CADJPY=X": "CAD/JPY", "CHFJPY=X": "CHF/JPY", "NZDJPY=X": "NZD/JPY",
    "EURGBP=X": "EUR/GBP", "EURAUD=X": "EUR/AUD", "EURCAD=X": "EUR/CAD",
    "EURCHF=X": "EUR/CHF", "EURNZD=X": "EUR/NZD",
    "GBPAUD=X": "GBP/AUD", "GBPCAD=X": "GBP/CAD", "GBPCHF=X": "GBP/CHF",
    "GBPNZD=X": "GBP/NZD",
    "AUDCAD=X": "AUD/CAD", "AUDCHF=X": "AUD/CHF", "AUDNZD=X": "AUD/NZD",
    "NZDCAD=X": "NZD/CAD", "NZDCHF=X": "NZD/CHF", "CADCHF=X": "CAD/CHF",
    "USDSEK=X": "USD/SEK", "USDNOK=X": "USD/NOK", "USDSGD=X": "USD/SGD",
    "USDMXN=X": "USD/MXN", "USDZAR=X": "USD/ZAR", "USDTRY=X": "USD/TRY",
    "USDPLN=X": "USD/PLN", "USDHUF=X": "USD/HUF", "USDCZK=X": "USD/CZK",
    "USDILS=X": "USD/ILS",
    "EURSEK=X": "EUR/SEK", "EURNOK=X": "EUR/NOK", "EURPLN=X": "EUR/PLN",
    "EURHUF=X": "EUR/HUF", "EURTRY=X": "EUR/TRY", "EURCZK=X": "EUR/CZK",
    "GBPSEK=X": "GBP/SEK", "GBPNOK=X": "GBP/NOK",
}

FX_SPREAD_PIPS = {
    "EURUSD=X": 0.8, "GBPUSD=X": 1.0, "USDJPY=X": 0.9, "USDCHF=X": 1.2,
    "AUDUSD=X": 1.0, "NZDUSD=X": 1.4, "USDCAD=X": 1.3,
    "EURJPY=X": 1.4, "GBPJPY=X": 2.2, "AUDJPY=X": 1.6, "CADJPY=X": 1.8,
    "CHFJPY=X": 2.2, "NZDJPY=X": 2.0,
    "EURGBP=X": 1.2, "EURAUD=X": 2.0, "EURCAD=X": 2.2, "EURCHF=X": 1.5,
    "EURNZD=X": 3.0,
    "GBPAUD=X": 3.0, "GBPCAD=X": 3.2, "GBPCHF=X": 2.6, "GBPNZD=X": 4.0,
    "AUDCAD=X": 1.8, "AUDCHF=X": 1.8, "AUDNZD=X": 2.2,
    "NZDCAD=X": 2.4, "NZDCHF=X": 2.6, "CADCHF=X": 2.2,
    "USDSEK=X": 22.0, "USDNOK=X": 25.0, "USDSGD=X": 3.0,
    "USDMXN=X": 35.0, "USDZAR=X": 60.0, "USDTRY=X": 90.0,
    "USDPLN=X": 25.0, "USDHUF=X": 6.0, "USDCZK=X": 30.0, "USDILS=X": 30.0,
    "EURSEK=X": 25.0, "EURNOK=X": 28.0, "EURPLN=X": 28.0,
    "EURHUF=X": 7.0, "EURTRY=X": 110.0, "EURCZK=X": 30.0,
    "GBPSEK=X": 40.0, "GBPNOK=X": 45.0,
}
FX_COMMISSION_PIPS = 0.7
FX_SLIPPAGE_PIPS = 0.3

# ---------------------------------------------------------------- US universe
# The S&P 500 is fetched at setup time so the list stays current; this hard-coded
# set is the fallback when there is no network, and covers the mega/large caps that
# dominate index weight anyway.
US_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B", "AVGO", "TSLA",
    "LLY", "JPM", "V", "XOM", "UNH", "MA", "COST", "HD", "PG", "JNJ",
    "WMT", "NFLX", "ABBV", "CRM", "BAC", "ORCL", "CVX", "MRK", "KO", "AMD",
    "PEP", "TMO", "LIN", "ADBE", "CSCO", "ACN", "MCD", "ABT", "PM", "IBM",
    "GE", "TXN", "QCOM", "DHR", "CAT", "VZ", "INTU", "NEE", "DIS", "RTX",
    "AMGN", "PFE", "CMCSA", "SPGI", "NOW", "UBER", "AXP", "UNP", "T", "LOW",
    "ISRG", "GS", "ETN", "PGR", "BKNG", "HON", "TJX", "COP", "BLK", "SYK",
    "VRTX", "MS", "C", "MU", "LMT", "ADI", "BSX", "MDT", "REGN", "ADP",
    "PLD", "MMC", "CB", "AMAT", "SBUX", "GILD", "DE", "SCHW", "BMY", "MDLZ",
    "SO", "ELV", "CI", "ZTS", "MO", "DUK", "ICE", "PANW", "KLAC", "SHW",
    "APH", "CME", "PYPL", "SNPS", "CDNS", "PH", "MSI", "TT", "EQIX", "CMG",
    "USB", "AON", "ITW", "NOC", "PNC", "MCK", "CSX", "WM", "ORLY", "MMM",
    "FDX", "GD", "APD", "MCO", "EMR", "NSC", "ROP", "AJG", "MAR", "SLB",
    "PCAR", "TGT", "ECL", "AZO", "SPG", "AFL", "TFC", "TRV", "CARR", "DHI",
    "PSA", "NXPI", "OKE", "SRE", "AIG", "ALL", "MET", "AMP", "F", "GM",
    "PRU", "KMB", "HLT", "DOW", "STZ", "IQV", "A", "CTAS", "PAYX", "ROST",
    "LRCX", "FTNT", "ADSK", "DXCM", "IDXX", "ODFL", "VRSK", "EA", "CPRT", "FAST",
    "COIN", "ABNB", "PLTR", "DASH", "SNOW", "CRWD", "DDOG", "TTD", "WDAY", "TEAM",
]

# Round-trip dealing cost in basis points of notional: half-spread each way plus
# slippage. Deliberately generous for a retail order in a liquid US name — a
# too-cheap cost model is the easiest way to fake an edge.
US_COST_BPS = 6.0


@functools.lru_cache(maxsize=4)
def us_universe():
    """S&P 500 constituents, cached to disk; falls back to the built-in list."""
    import json
    base = os.path.join(HERE, "data", "us")
    # meta.json lists only the tickers that actually downloaded; universe.json is the
    # wish list. Trading the wish list means opening a CSV that is not there.
    for path, pick in ((os.path.join(base, "meta.json"), True),
                       (os.path.join(base, "universe.json"), False)):
        if not os.path.exists(path):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
            if len(d) > 50:
                return ({k: v.get("name", k) for k, v in d.items()} if pick else d)
        except Exception:
            pass
    return {t: t for t in US_FALLBACK}


# ---------------------------------------------------------------- TW universe
# Taiwan dealing cost is the headline difference and it is brutal: 證交稅 0.3% on the
# sell alone, plus 手續費 0.1425% each way (assume a 6折 online discount -> 0.0855%).
# Round trip = 0.0855 x2 + 0.30 = 0.471%, call it 50bps with slippage. That is EIGHT
# TIMES the US figure, and it is why a strategy that works on the S&P can still lose
# money on the same setups in Taipei.
# 手續費 is 0.1425% list price, and every broker discounts it for electronic orders.
# The discount is the only part of Taiwanese cost you can actually negotiate, so it
# is a parameter rather than a constant — set TW_FEE_DISCOUNT to your own rate.
#   1.00 = 牌告 (no discount)      0.60 = 6折
#   0.38 = 3.8折 (common online)   0.28 = 2.8折 (high volume)
# Default stays at 6折 so the live scanner and the backtest agree; the sweep was
# run at that rate. Set the env var to your real rate — but note the ceiling:
# even 2.8折 only takes 50.1bps down to 41.0, because 30bps of it is tax.
TW_FEE_DISCOUNT = float(os.environ.get("TW_FEE_DISCOUNT", 0.60))
TW_FEE_LIST_BPS = 14.25           # 0.1425% 牌告手續費, each way
TW_COMMISSION_BPS = TW_FEE_LIST_BPS * TW_FEE_DISCOUNT * 2
TW_TAX_BPS = 30.0                 # 證交稅 0.3% — sell side only, and NOT negotiable
TW_SLIPPAGE_BPS = 3.0             # chunky tick sizes
TW_COST_BPS = TW_COMMISSION_BPS + TW_TAX_BPS + TW_SLIPPAGE_BPS

# The tax alone is 5x the entire US round trip. No amount of parameter tuning gets
# around it, which is why the honest lever for Taiwan is a WIDER stop: cost is a
# fixed share of notional, so cost_r = cost/risk shrinks as the stop grows.

TW_FALLBACK = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2308.TW": "台達電",
    "2382.TW": "廣達", "2891.TW": "中信金", "2881.TW": "富邦金", "2412.TW": "中華電",
    "1301.TW": "台塑", "1303.TW": "南亞", "2002.TW": "中鋼", "2303.TW": "聯電",
    "3711.TW": "日月光投控", "2886.TW": "兆豐金", "2884.TW": "玉山金", "2357.TW": "華碩",
    "3034.TW": "聯詠", "2379.TW": "瑞昱", "2603.TW": "長榮", "2609.TW": "陽明",
    "2615.TW": "萬海", "1216.TW": "統一", "2207.TW": "和泰車", "2892.TW": "第一金",
    "5880.TW": "合庫金", "2885.TW": "元大金", "2883.TW": "凱基金", "2887.TW": "台新金",
    "6505.TW": "台塑化", "3008.TW": "大立光", "4938.TW": "和碩", "2377.TW": "微星",
    "2376.TW": "技嘉", "3231.TW": "緯創", "2356.TW": "英業達", "2324.TW": "仁寶",
    "2301.TW": "光寶科", "3443.TW": "創意", "5269.TW": "祥碩", "8046.TW": "南電",
    "3037.TW": "欣興", "6669.TW": "緯穎", "2345.TW": "智邦", "3661.TW": "世芯-KY",
    "2049.TW": "上銀", "1590.TW": "亞德客-KY", "1476.TW": "儒鴻", "2105.TW": "正新",
    "2201.TW": "裕隆", "2618.TW": "長榮航", "2610.TW": "華航", "2912.TW": "統一超",
    "5876.TW": "上海商銀", "2890.TW": "永豐金", "2880.TW": "華南金", "1101.TW": "台泥",
    "1102.TW": "亞泥", "1326.TW": "台化", "2408.TW": "南亞科", "6239.TW": "力成",
    "2337.TW": "旺宏", "2344.TW": "華邦電", "8069.TW": "元太", "6176.TW": "瑞儀",
    "2385.TW": "群光", "1519.TW": "華城", "1513.TW": "中興電", "2371.TW": "大同",
}

MIN_TW_TURNOVER = 50_000_000      # NT$50m a day — below this the spread eats the edge


def tw_fetch_universe():
    """Listed TWSE names above a liquidity floor, from TWSE open data.

    Screened on turnover because the strategy needs to actually get filled: a stock
    trading NT$5m a day cannot absorb a position, and its spread would swamp an
    edge measured in tenths of an R.
    """
    import json, urllib.request, re
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    rows = json.loads(urllib.request.urlopen(req, timeout=30).read())
    uni = {}
    for r in rows:
        code, name = str(r.get("Code", "")), str(r.get("Name", "")).strip()
        # 4-digit codes that do not start with 0: 00xx is ETFs, 5-6 digits are
        # warrants and beneficiary certificates. We want ordinary shares only.
        if not re.fullmatch(r"[1-9]\d{3}", code):
            continue
        try:
            if float(str(r.get("TradeValue", "0")).replace(",", "")) < MIN_TW_TURNOVER:
                continue
        except ValueError:
            continue
        uni[f"{code}.TW"] = name or code
    return uni


@functools.lru_cache(maxsize=4)
def tw_universe():
    import json
    base = os.path.join(HERE, "data", "tw")
    for path, from_meta in ((os.path.join(base, "meta.json"), True),
                            (os.path.join(base, "universe.json"), False)):
        if not os.path.exists(path):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
            if len(d) > 30:
                return ({k: v.get("name", k) for k, v in d.items()} if from_meta else d)
        except Exception:
            pass
    return dict(TW_FALLBACK)


# ---------------------------------------------------------------- CM universe
# Commodity and metal CFDs, as listed on Bitget's TradFi board. All are Yahoo futures
# front months except the four gold/silver crosses, which Yahoo does not carry and are
# DERIVED — XAU/EUR is literally XAU/USD divided by EUR/USD, which is how a broker
# prices it too.
CM_DIRECT = {
    "GC=F": "Gold (XAU/USD)", "SI=F": "Silver (XAG/USD)",
    "PL=F": "Platinum/USD", "PA=F": "Palladium/USD", "HG=F": "Copper",
    "CL=F": "WTI Crude Oil", "BZ=F": "Brent Crude Oil",
    "NG=F": "Natural Gas", "RB=F": "Gasoline",
    "ZW=F": "US Wheat (SRW)", "ZS=F": "Soybean", "CT=F": "Cotton",
    "KC=F": "Arabica Coffee", "SB=F": "Sugar", "CC=F": "US Cocoa",
    "OJ=F": "Orange Juice",
}

# synthetic = numerator / denominator, both fetched from their own market
CM_DERIVED = {
    "XAUEUR": ("Gold/EUR", "GC=F", "EURUSD=X", "div"),
    "XAUJPY": ("Gold/JPY", "GC=F", "USDJPY=X", "mul"),
    "XAUAUD": ("Gold/AUD", "GC=F", "AUDUSD=X", "div"),
    "XAGAUD": ("Silver/AUD", "SI=F", "AUDUSD=X", "div"),
}

CM_UNIVERSE = {**CM_DIRECT, **{k: v[0] for k, v in CM_DERIVED.items()}}

# CFD round trip in basis points of notional. Metals and oil are tight; the softs are
# not, and a 40bp round trip on cocoa is the difference between an edge and a fee.
CM_COST_BPS = {
    "GC=F": 4.0, "SI=F": 9.0, "PL=F": 16.0, "PA=F": 22.0, "HG=F": 12.0,
    "CL=F": 6.0, "BZ=F": 7.0, "NG=F": 18.0, "RB=F": 16.0,
    "ZW=F": 20.0, "ZS=F": 16.0, "CT=F": 22.0, "KC=F": 28.0,
    "SB=F": 24.0, "CC=F": 40.0, "OJ=F": 45.0,
    "XAUEUR": 8.0, "XAUJPY": 8.0, "XAUAUD": 10.0, "XAGAUD": 16.0,
}
CM_DEFAULT_BPS = 20.0


def cm_universe():
    return dict(CM_UNIVERSE)


MARKETS = {
    "fx": {
        "name": "外匯", "label": "FX",
        "session": "24/5", "tf_stack": {"1h": ("4h", "1d"), "4h": ("1d", "1w"),
                                        "1d": ("1w", "1mo")},
    },
    "us": {
        "name": "美股", "label": "US Equities",
        "session": "09:30-16:00 ET", "tf_stack": {"1h": ("4h", "1d"),
                                                  "4h": ("1d", "1w"),
                                                  "1d": ("1w", "1mo")},
    },
    "cm": {
        "name": "商品金屬", "label": "Commodities & Metals",
        "session": "近 24/5", "tf_stack": {"1h": ("4h", "1d"), "4h": ("1d", "1w"),
                                          "1d": ("1w", "1mo")},
    },
    "tw": {
        "name": "台股", "label": "TW Equities",
        "session": "09:00-13:30 TPE", "tf_stack": {"1h": ("4h", "1d"),
                                                   "4h": ("1d", "1w"),
                                                   "1d": ("1w", "1mo")},
    },
}
