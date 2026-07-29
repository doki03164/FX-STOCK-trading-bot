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
}
