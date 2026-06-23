"""Chart Renderer — TradingView chart via Chart-IMG.

The intraday chart is rendered by Chart-IMG (https://chart-img.com), which produces a
real TradingView chart server-side and returns a PNG. We send the symbol, timeframe,
EMA 20/50/200 studies, and the key support/resistance levels as horizontal-line
drawings. Chart-IMG fetches its own TradingView price data, so this needs no local
candles (and no numpy/pandas/matplotlib — that stack is removed from this project).

Requires CHARTIMG_API_KEY. A Chart-IMG paid tier is needed for studies + drawings +
larger sizes; link your TradingView Premium in the Chart-IMG dashboard to use your own
layouts/indicators. If the key is absent or the request fails, render() returns None and
the intraday job still publishes the text plan (chart marked unavailable).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import requests

from config import config

log = logging.getLogger("charts")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "charts_out")

CHARTIMG_URL = "https://api.chart-img.com/v2/tradingview/advanced-chart"

# FMP timeframe slug -> Chart-IMG / TradingView interval.
_INTERVAL_MAP = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
                 "1hour": "1h", "4hour": "4h", "1day": "1D"}

# Snapshot level -> (TradingView line color, label).
_LEVELS = [
    ("previous_day.high", "#c0392b", "Prev Day High"),
    ("previous_day.low", "#27ae60", "Prev Day Low"),
    ("today.asian_session_high", "#e67e22", "Asian High"),
    ("today.asian_session_low", "#2980b9", "Asian Low"),
    ("today.open", "#7f8c8d", "Day Open"),
]


def _dig(snapshot: Dict, dotted: str):
    cur = snapshot
    for part in dotted.split("."):
        cur = (cur or {}).get(part)
    return cur


def _interval() -> str:
    return _INTERVAL_MAP.get(config.intraday_tf, "5m")


def _drawings(snapshot: Dict) -> List[Dict]:
    drawings = []
    for key, color, label in _LEVELS:
        val = _dig(snapshot, key)
        if val is not None:
            drawings.append({
                "name": "Horizontal Line",
                "input": {"price": float(val)},
                "options": {"text": label, "lineColor": color, "textColor": color,
                            "showPrice": True},
            })
    return drawings


def render(snapshot: Dict) -> Optional[str]:
    """Render the TradingView chart via Chart-IMG. Returns the PNG path, or None."""
    if not config.chartimg_api_key:
        log.warning("CHARTIMG_API_KEY not set — chart skipped (text plan still sent)")
        return None

    payload = {
        "symbol": config.chartimg_symbol,
        "interval": _interval(),
        "theme": "dark",
        "width": config.chartimg_width,
        "height": config.chartimg_height,
        "studies": [
            {"name": "Moving Average Exponential", "input": {"length": 20}},
            {"name": "Moving Average Exponential", "input": {"length": 50}},
            {"name": "Moving Average Exponential", "input": {"length": 200}},
        ],
        "drawings": _drawings(snapshot),
    }

    try:
        resp = requests.post(
            CHARTIMG_URL,
            headers={"x-api-key": config.chartimg_api_key, "content-type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        log.warning("Chart-IMG request failed: %s", exc)
        return None

    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not ctype.startswith("image"):
        body = resp.text[:300] if not ctype.startswith("image") else "<image>"
        log.warning("Chart-IMG error %s (%s): %s", resp.status_code, config.chartimg_symbol, body)
        return None

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now(config.tz).strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUTPUT_DIR, f"{config.instrument}_{config.intraday_tf_label}_{stamp}.png")
    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    log.info("Chart-IMG saved: %s (%s %s, %d levels)",
             out_path, config.chartimg_symbol, _interval(), len(payload["drawings"]))
    return out_path
