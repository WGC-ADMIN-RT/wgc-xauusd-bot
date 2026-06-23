"""Market Data Service — XAU/USD candles, indicators, and session levels.

Pulls intraday candles from FMP (`/stable/historical-chart/<interval>`), computes
EMA 20/50/200, ATR(14), and derives previous-day / Asian-session / current-day levels
in SGT. Produces the structured snapshot the intraday analysis (Task 2) consumes.

PURE PYTHON — no numpy/pandas. This host (shared cPanel, RLIMIT_NPROC ~1400) cannot
keep numpy's OpenBLAS backend alive: the import spawns a thread burst that trips the
process cap and truncates the on-disk package. So indicators are computed with plain
Python here, and the chart is rendered externally via Chart-IMG (TradingView). The
math matches the previous pandas implementation exactly:
  * EMA  = ewm(span=period, adjust=False)  -> alpha = 2/(period+1), seeded at series[0]
  * ATR  = Wilder ewm(alpha=1/period, adjust=False) over True Range, seeded at tr[0]

Timezone note: FMP intraday timestamps for forex are treated as UTC (FMP_CHART_TZ) and
converted to SGT for session logic.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config

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


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

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


def _parse_candles(raw: List[dict]) -> List[Dict]:
    """FMP rows -> list of candle dicts, oldest→newest, with SGT/UTC datetimes."""
    out: List[Dict] = []
    for r in raw:
        try:
            dt = datetime.strptime(str(r["date"]).strip(), "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError, TypeError):
            continue
        dt_utc = FMP_CHART_TZ.localize(dt)
        try:
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            vol = float(r.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        out.append({
            "dt_utc": dt_utc,
            "dt_sgt": dt_utc.astimezone(config.tz),
            "open": o, "high": h, "low": l, "close": c, "volume": vol,
        })
    out.sort(key=lambda x: x["dt_utc"])
    return out


def get_candles(interval: str, limit: int = 250, symbol: Optional[str] = None) -> List[Dict]:
    """Return up to `limit` most-recent candle dicts, oldest→newest."""
    symbol = symbol or config.instrument
    candles = _parse_candles(_request(interval, symbol, limit))
    return candles[-limit:]


# ---------------------------------------------------------------------------
# Pure-Python indicators (match pandas ewm semantics)
# ---------------------------------------------------------------------------

def _ema_series(values: List[float], period: int) -> List[float]:
    """ewm(span=period, adjust=False) series. alpha=2/(period+1), seeded at values[0]."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: List[float], period: int) -> Optional[float]:
    s = _ema_series(values, period)
    return s[-1] if s else None


def atr(candles: List[Dict], period: int = 14) -> Optional[float]:
    """Wilder ATR: ewm(alpha=1/period, adjust=False) over True Range, seeded at tr[0]."""
    if not candles:
        return None
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in candles:
        if prev_close is None:
            tr = c["high"] - c["low"]
        else:
            tr = max(c["high"] - c["low"], abs(c["high"] - prev_close), abs(c["low"] - prev_close))
        trs.append(tr)
        prev_close = c["close"]
    alpha = 1.0 / period
    a = trs[0]
    for tr in trs[1:]:
        a = tr * alpha + a * (1 - alpha)
    return a


def _h4_trend(symbol: str) -> str:
    """Bullish/bearish/range from H4 close vs EMA50 and its slope."""
    try:
        h4 = get_candles(H4, limit=120, symbol=symbol)
    except MarketDataError:
        return "range"
    if len(h4) < 55:
        return "range"
    closes = [c["close"] for c in h4]
    e50 = _ema_series(closes, 50)
    last_close = closes[-1]
    last_e, prev_e = e50[-1], e50[-10]
    rising, falling = last_e > prev_e, last_e < prev_e
    if last_close > last_e and rising:
        return "bullish"
    if last_close < last_e and falling:
        return "bearish"
    return "range"


# ---------------------------------------------------------------------------
# Session levels (SGT)
# ---------------------------------------------------------------------------

def _hl(candles: List[Dict], field: str) -> Optional[float]:
    if not candles:
        return None
    if field == "high":
        val = max(c["high"] for c in candles)
    elif field == "low":
        val = min(c["low"] for c in candles)
    elif field == "close":
        val = candles[-1]["close"]
    elif field == "open":
        val = candles[0]["open"]
    else:
        return None
    return round(float(val), 2)


def _session_levels(candles: List[Dict]) -> Dict:
    """Previous-day, today, and Asian-session levels (SGT)."""
    by_date: Dict = defaultdict(list)
    for c in candles:
        by_date[c["dt_sgt"].date()].append(c)
    dates = sorted(by_date)
    today = dates[-1]
    todays = by_date[today]

    prior = [d for d in dates if d < today]
    prev = by_date[prior[-1]] if prior else []  # handles weekend/holiday gaps

    asian = [c for c in todays
             if ASIAN_SESSION_START_H <= c["dt_sgt"].hour < ASIAN_SESSION_END_H
             or (c["dt_sgt"].hour == ASIAN_SESSION_END_H and c["dt_sgt"].minute == 0)]

    return {
        "previous_day": {"high": _hl(prev, "high"), "low": _hl(prev, "low"), "close": _hl(prev, "close")},
        "today": {
            "open": _hl(todays, "open"),
            "asian_session_high": _hl(asian, "high"),
            "asian_session_low": _hl(asian, "low"),
            "current_day_high": _hl(todays, "high"),
            "current_day_low": _hl(todays, "low"),
        },
    }


# ---------------------------------------------------------------------------
# Snapshot + price reaction
# ---------------------------------------------------------------------------

def build_snapshot(upcoming_usd_news: Optional[List[dict]] = None) -> Dict:
    """Assemble the intraday data package (matches the spec's JSON shape)."""
    symbol = config.instrument
    tf, tf_label = config.intraday_tf, config.intraday_tf_label
    candles = get_candles(tf, limit=200, symbol=symbol)
    if len(candles) < 50:
        raise MarketDataError(f"Insufficient {tf_label} candles ({len(candles)})")
    h1 = get_candles(H1, limit=250, symbol=symbol)

    closes = [c["close"] for c in candles]
    h1_closes = [c["close"] for c in h1]
    levels = _session_levels(candles)
    current_price = round(float(candles[-1]["close"]), 2)

    def _r(x):
        return round(float(x), 2) if x is not None else None

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
            "ema20": _r(ema(closes, 20)),
            "ema50": _r(ema(closes, 50)),
            "ema200": _r(ema(closes, 200)) if len(candles) >= 200 else None,
            "atr14": _r(atr(candles, 14)),
            "h1_ema50": _r(ema(h1_closes, 50)) if len(h1) >= 50 else None,
            "h1_ema200": _r(ema(h1_closes, 200)) if len(h1) >= 200 else None,
            "h4_trend": _h4_trend(symbol),
        },
        "upcoming_usd_news": upcoming_usd_news or [],
        "_candles": candles,  # kept in-memory for swing-level detection; not serialized to DB
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
    except Exception as exc:  # MarketDataError or any transient fetch failure
        log.warning("price reaction unavailable: %s", exc)
        return {"price_before": None, "current_price": None, "price_change": "—"}

    before = [c for c in m5 if c["dt_sgt"] < release_sgt]
    price_before = round(float(before[-1]["close"]), 2) if before else None
    current = round(float(m5[-1]["close"]), 2)
    if price_before:
        chg = current - price_before
        pct = chg / price_before * 100
        change_str = f"{chg:+.2f} ({pct:+.2f}%)"
    else:
        change_str = "—"
    return {"price_before": price_before, "current_price": current, "price_change": change_str}
