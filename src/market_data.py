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


def _swing_highs_lows(candles: List[Dict], window: int = 5):
    """Recent swing highs / lows (oldest→newest within the returned tail)."""
    highs: List[float] = []
    lows: List[float] = []
    if len(candles) < window * 2 + 1:
        return highs, lows
    h = [c["high"] for c in candles]
    l = [c["low"] for c in candles]
    n = len(candles)
    for i in range(window, n - window):
        if h[i] == max(h[i - window:i + window + 1]):
            highs.append(round(h[i], 2))
        if l[i] == min(l[i - window:i + window + 1]):
            lows.append(round(l[i], 2))
    return highs, lows


def _h1_trend(h1_candles: List[Dict]) -> str:
    """Bullish/bearish/range from H1 close vs EMA50 and its slope."""
    if len(h1_candles) < 55:
        return "range"
    closes = [c["close"] for c in h1_candles]
    e50 = _ema_series(closes, 50)
    last_close = closes[-1]
    last_e, prev_e = e50[-1], e50[-10]
    rising, falling = last_e > prev_e, last_e < prev_e
    if last_close > last_e and rising:
        return "bullish"
    if last_close < last_e and falling:
        return "bearish"
    return "range"


def _price_zone(center: float, atr_val: Optional[float], reason: str) -> Dict:
    """Turn a single level into a supply/demand zone range (never a single tick)."""
    half = max((atr_val or 1.0) * 0.18, center * 0.0009, 0.5)
    lo, hi = round(center - half, 2), round(center + half, 2)
    return {"low": lo, "high": hi, "reason": reason}


def _dedupe_zones(zones: List[Dict], price: float) -> List[Dict]:
    """Merge zones whose midpoints are within ~0.12% of each other."""
    out: List[Dict] = []
    for z in sorted(zones, key=lambda x: (x["high"] + x["low"]) / 2):
        mid = (z["high"] + z["low"]) / 2
        if any(abs(mid - (o["high"] + o["low"]) / 2) / max(mid, 1) < 0.0012 for o in out):
            continue
        out.append(z)
    return out


def _zone_hints(m5: List[Dict], h1: List[Dict], levels: Dict, price: float) -> Dict:
    """Pre-computed supply/demand zone hints for the AI (M5 + H1 + session levels)."""
    m5_atr = atr(m5, 14)
    h1_atr = atr(h1, 14) if h1 else m5_atr
    pd_ = levels["previous_day"]
    td = levels["today"]
    demand: List[Dict] = []
    supply: List[Dict] = []

    def add_d(val, reason):
        if val is not None and float(val) < price:
            demand.append(_price_zone(float(val), m5_atr, reason))

    def add_s(val, reason):
        if val is not None and float(val) > price:
            supply.append(_price_zone(float(val), m5_atr, reason))

    add_d(pd_.get("low"), "previous day low / demand")
    add_d(td.get("asian_session_low"), "Asian session low / liquidity (LQ)")
    add_d(td.get("current_day_low"), "intraday low / demand")
    add_s(pd_.get("high"), "previous day high / supply")
    add_s(td.get("asian_session_high"), "Asian session high / liquidity")
    add_s(td.get("current_day_high"), "intraday high / supply")

    m5_hi, m5_lo = _swing_highs_lows(m5)
    h1_hi, h1_lo = _swing_highs_lows(h1)
    for v in m5_lo[-4:]:
        if v < price:
            demand.append(_price_zone(v, m5_atr, "M5 swing low / demand"))
    for v in m5_hi[-4:]:
        if v > price:
            supply.append(_price_zone(v, m5_atr, "M5 swing high / supply"))
    for v in h1_lo[-3:]:
        if v < price:
            demand.append(_price_zone(v, h1_atr, "H1 demand / structure low"))
    for v in h1_hi[-3:]:
        if v > price:
            supply.append(_price_zone(v, h1_atr, "H1 supply / structure high"))

    demand = _dedupe_zones(demand, price)
    supply = _dedupe_zones(supply, price)
    demand.sort(key=lambda z: z["high"], reverse=True)
    supply.sort(key=lambda z: z["low"])
    return {
        "demand": demand[:3],
        "supply": supply[:3],
    }


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
    if len(h1) < 50:
        raise MarketDataError(f"Insufficient H1 candles ({len(h1)})")

    closes = [c["close"] for c in candles]
    h1_closes = [c["close"] for c in h1]
    levels = _session_levels(candles)
    current_price = round(float(candles[-1]["close"]), 2)
    h1_hi, h1_lo = _swing_highs_lows(h1)
    zones = _zone_hints(m5=candles, h1=h1, levels=levels, price=current_price)

    def _r(x):
        return round(float(x), 2) if x is not None else None

    snapshot = {
        "instrument": symbol,
        "timestamp_sgt": datetime.now(config.tz).strftime("%Y-%m-%d %H:%M:%S"),
        "execution_timeframe": "M5",
        "bias_timeframe": "H1",
        "screenshot_timeframe": tf_label,
        "candles_in_screenshot": int(len(candles)),
        "current_price": current_price,
        "spread": None,
        "previous_day": levels["previous_day"],
        "today": levels["today"],
        "h1_structure": {
            "trend": _h1_trend(h1),
            "structure_high": h1_hi[-1] if h1_hi else None,
            "structure_low": h1_lo[-1] if h1_lo else None,
        },
        "zone_hints": zones,
        "indicators": {
            "m5_ema20": _r(ema(closes, 20)),
            "m5_ema50": _r(ema(closes, 50)),
            "m5_ema200": _r(ema(closes, 200)) if len(candles) >= 200 else None,
            "m5_atr14": _r(atr(candles, 14)),
            "h1_ema50": _r(ema(h1_closes, 50)) if len(h1) >= 50 else None,
            "h1_ema200": _r(ema(h1_closes, 200)) if len(h1) >= 200 else None,
            "h1_trend": _h1_trend(h1),
        },
        "upcoming_usd_news": upcoming_usd_news or [],
        "_candles": candles,
        "_h1_candles": h1,
    }
    log.info("Snapshot built (M5/H1): price=%s h1=%s demand=%d supply=%d",
             current_price, snapshot["h1_structure"]["trend"],
             len(zones["demand"]), len(zones["supply"]))
    return snapshot


def _close_before(candles: List[dict], target_sgt: datetime) -> Optional[float]:
    before = [c for c in candles if c["dt_sgt"] < target_sgt]
    return round(float(before[-1]["close"]), 2) if before else None


def _close_at_or_after(candles: List[dict], target_sgt: datetime) -> Optional[float]:
    at_or_after = [c for c in candles if c["dt_sgt"] >= target_sgt]
    return round(float(at_or_after[0]["close"]), 2) if at_or_after else None


def _move_str(from_price: Optional[float], to_price: Optional[float]) -> str:
    if from_price is None or to_price is None:
        return "—"
    chg = to_price - from_price
    pct = chg / from_price * 100
    return f"{chg:+.2f} ({pct:+.2f}%)"


def price_action_confirmation(
    xau_bias: str,
    price_before: Optional[float],
    price_ref: Optional[float],
) -> str:
    """Whether the move from pre-release to price_ref confirms the XAUUSD data read."""
    if price_before is None or price_ref is None:
        return "Price confirmation pending — need more candle data."
    move = price_ref - price_before
    if xau_bias == "neutral":
        return "Mixed/in-line data — wait for a clear M5 confirmation candle."
    if abs(move) < 0.05:
        return "Price action is flat so far — wait for confirmation."
    expected_up = xau_bias == "bullish"
    if (move > 0 and expected_up) or (move < 0 and not expected_up):
        return "Price action confirms the data read."
    return "Price action rejects the data read so far — wait for confirmation."


def get_price_reaction(
    release_utc: datetime,
    now_utc: Optional[datetime] = None,
    symbol: Optional[str] = None,
) -> Dict:
    """M5 price snapshots around a news release for the post-release Telegram block."""
    symbol = symbol or config.instrument
    release_sgt = release_utc.astimezone(config.tz)
    now_utc = now_utc or datetime.now(pytz.UTC)
    now_sgt = now_utc.astimezone(config.tz)
    empty = {
        "price_before": None,
        "price_at_release": None,
        "price_plus_5": None,
        "price_plus_15": None,
        "current_price": None,
        "price_change": "—",
    }
    try:
        m5 = get_candles("5min", limit=200, symbol=symbol)
    except Exception as exc:  # MarketDataError or any transient fetch failure
        log.warning("price reaction unavailable: %s", exc)
        return empty

    t_plus_5 = release_sgt + timedelta(minutes=5)
    t_plus_15 = release_sgt + timedelta(minutes=15)
    price_before = _close_before(m5, release_sgt)
    price_at_release = _close_at_or_after(m5, release_sgt)
    price_plus_5 = _close_at_or_after(m5, t_plus_5) if now_sgt >= t_plus_5 else None
    price_plus_15 = _close_at_or_after(m5, t_plus_15) if now_sgt >= t_plus_15 else None
    current = round(float(m5[-1]["close"]), 2)

    return {
        "price_before": price_before,
        "price_at_release": price_at_release,
        "price_plus_5": price_plus_5,
        "price_plus_15": price_plus_15,
        "current_price": current,
        "price_change": _move_str(price_before, current),
    }
