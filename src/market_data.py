"""Market Data Service — XAU/USD candles, indicators, and session levels.

Pulls intraday candles from FMP (`/stable/historical-chart/<interval>`), computes
EMA 20/50/200, ATR(14), and derives previous-day / Asian-session / current-day levels
in SGT. Produces the structured snapshot the intraday analysis (Task 2) consumes.

Timezone note: FMP intraday timestamps for forex are treated as UTC (FMP_CHART_TZ) and
converted to SGT for session logic. Verify against a known candle on the first live pull.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config

# NOTE: pandas/numpy are imported LAZILY inside the functions that need them, not at
# module top. On this shared cPanel account (RLIMIT_NPROC ~1400) the every-minute
# news cron imports this module constantly; importing numpy that often under the
# process cap kept truncating the install ("numpy.lib.utils missing"). With lazy
# imports, numpy is only loaded by the once-daily intraday snapshot and by an actual
# post-release price reaction — and `from __future__ import annotations` keeps the
# `pd.DataFrame` type hints valid without importing pandas at module load.

log = logging.getLogger("market_data")

FMP_BASE = "https://financialmodelingprep.com"
FMP_CHART_TZ = pytz.UTC

# Asian (Tokyo) session window in SGT used for the session high/low.
ASIAN_SESSION_START_H = 8
ASIAN_SESSION_END_H = 16

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "wgc-xauusd-bot/1.0"})

# FMP interval slugs
M15, H1, H4 = "15min", "1hour", "4hour"


class MarketDataError(Exception):
    pass


@retry(
    retry=retry_if_exception_type((requests.RequestException, MarketDataError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=True,
)
def _request(interval: str, symbol: str, limit: int) -> List[dict]:
    if not config.fmp_api_key:
        raise MarketDataError("FMP_API_KEY not configured")
    url = f"{FMP_BASE}/stable/historical-chart/{interval}"
    params = {"symbol": symbol, "apikey": config.fmp_api_key}
    resp = _SESSION.get(url, params=params, timeout=30)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise MarketDataError(f"FMP transient error {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("Error Message"):
        raise MarketDataError(f"FMP: {data['Error Message']}")
    if not isinstance(data, list) or not data:
        raise MarketDataError(f"No candle data for {symbol} {interval}")
    return data


def get_candles(interval: str, limit: int = 250, symbol: Optional[str] = None) -> pd.DataFrame:
    """Return an OHLCV DataFrame indexed by SGT datetime, oldest→newest, trimmed to `limit`."""
    import pandas as pd  # lazy: keeps numpy out of the every-minute news import path
    symbol = symbol or config.instrument
    raw = _request(interval, symbol, limit)
    df = pd.DataFrame(raw)
    df["dt_utc"] = pd.to_datetime(df["date"]).dt.tz_localize(FMP_CHART_TZ)
    df["dt_sgt"] = df["dt_utc"].dt.tz_convert(config.tz)
    df = df.rename(columns=str.lower)[["dt_utc", "dt_sgt", "open", "high", "low", "close", "volume"]]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("dt_utc")
    df = df.set_index("dt_sgt")
    return df.tail(limit)


def ema(series: pd.Series, period: int) -> float:
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def atr(df: pd.DataFrame, period: int = 14) -> float:
    import pandas as pd  # lazy (see module note)
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    # Wilder smoothing
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def _h4_trend(symbol: str) -> str:
    """Bullish/bearish/range from H4 close vs EMA50 and its slope."""
    try:
        h4 = get_candles(H4, limit=120, symbol=symbol)
        if len(h4) < 55:
            return "range"
        e50 = h4["close"].ewm(span=50, adjust=False).mean()
        last_close = float(h4["close"].iloc[-1])
        last_e, prev_e = float(e50.iloc[-1]), float(e50.iloc[-10])
        rising, falling = last_e > prev_e, last_e < prev_e
        if last_close > last_e and rising:
            return "bullish"
        if last_close < last_e and falling:
            return "bearish"
        return "range"
    except MarketDataError:
        return "range"


def _session_levels(m15: pd.DataFrame) -> Dict:
    """Previous-day, today, and Asian-session levels (SGT)."""
    idx_dates = m15.index.normalize()
    today = m15.index.max().normalize()
    yesterday = today - timedelta(days=1)

    prev = m15[idx_dates == yesterday]
    if prev.empty:  # weekend/holiday gap — use the most recent prior day present
        prior_days = sorted(d for d in idx_dates.unique() if d < today)
        if prior_days:
            prev = m15[idx_dates == prior_days[-1]]

    todays = m15[idx_dates == today]
    asian = todays.between_time(f"{ASIAN_SESSION_START_H:02d}:00", f"{ASIAN_SESSION_END_H:02d}:00")

    def hl(df, field):
        if df.empty:
            return None
        return round(float({"high": df["high"].max(), "low": df["low"].min(),
                            "close": df["close"].iloc[-1], "open": df["open"].iloc[0]}[field]), 2)

    return {
        "previous_day": {"high": hl(prev, "high"), "low": hl(prev, "low"), "close": hl(prev, "close")},
        "today": {
            "open": hl(todays, "open"),
            "asian_session_high": hl(asian, "high"),
            "asian_session_low": hl(asian, "low"),
            "current_day_high": hl(todays, "high"),
            "current_day_low": hl(todays, "low"),
        },
    }


def build_snapshot(upcoming_usd_news: Optional[List[dict]] = None) -> Dict:
    """Assemble the intraday data package (matches the spec's JSON shape)."""
    symbol = config.instrument
    tf, tf_label = config.intraday_tf, config.intraday_tf_label
    candles = get_candles(tf, limit=200, symbol=symbol)
    if len(candles) < 50:
        raise MarketDataError(f"Insufficient {tf_label} candles ({len(candles)})")
    h1 = get_candles(H1, limit=250, symbol=symbol)

    levels = _session_levels(candles)
    current_price = round(float(candles["close"].iloc[-1]), 2)

    snapshot = {
        "instrument": symbol,
        "timestamp_sgt": datetime.now(config.tz).strftime("%Y-%m-%d %H:%M:%S"),
        "screenshot_timeframe": tf_label,
        "candles_in_screenshot": int(len(candles)),
        "current_price": current_price,
        "spread": None,  # not provided by FMP candles; populated later from broker feed
        "previous_day": levels["previous_day"],
        "today": levels["today"],
        "indicators": {
            "ema20": round(ema(candles["close"], 20), 2),
            "ema50": round(ema(candles["close"], 50), 2),
            "ema200": round(ema(candles["close"], 200), 2) if len(candles) >= 200 else None,
            "atr14": round(atr(candles, 14), 2),
            "h1_ema50": round(ema(h1["close"], 50), 2) if len(h1) >= 50 else None,
            "h1_ema200": round(ema(h1["close"], 200), 2) if len(h1) >= 200 else None,
            "h4_trend": _h4_trend(symbol),
        },
        "upcoming_usd_news": upcoming_usd_news or [],
        "_candles_df": candles,  # kept in-memory for the chart renderer; not serialized to DB
    }
    log.info("Snapshot built (%s): price=%s ema20=%s ema50=%s h4=%s",
             tf_label, current_price, snapshot["indicators"]["ema20"],
             snapshot["indicators"]["ema50"], snapshot["indicators"]["h4_trend"])
    return snapshot


def get_price_reaction(release_utc: datetime, symbol: Optional[str] = None) -> Dict:
    """Price just before the release vs current, for the post-news XAUUSD reaction block."""
    symbol = symbol or config.instrument
    release_sgt = release_utc.astimezone(config.tz)
    try:
        m5 = get_candles("5min", limit=200, symbol=symbol)
    except Exception as exc:  # MarketDataError, or a transient pandas/numpy import failure
        log.warning("price reaction unavailable: %s", exc)
        return {"price_before": None, "current_price": None, "price_change": "—"}

    before = m5[m5.index < release_sgt]
    price_before = round(float(before["close"].iloc[-1]), 2) if len(before) else None
    current = round(float(m5["close"].iloc[-1]), 2)
    if price_before:
        chg = current - price_before
        pct = chg / price_before * 100
        change_str = f"{chg:+.2f} ({pct:+.2f}%)"
    else:
        change_str = "—"
    return {"price_before": price_before, "current_price": current, "price_change": change_str}
