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


def _save_png(resp, label: str) -> Optional[str]:
    """Validate a Chart-IMG response and save the PNG. Returns path or None."""
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not ctype.startswith("image"):
        body = resp.text[:300] if not ctype.startswith("image") else "<image>"
        log.warning("Chart-IMG error %s (%s): %s", resp.status_code, label, body)
        return None
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now(config.tz).strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUTPUT_DIR, f"{config.instrument}_{config.intraday_tf_label}_{stamp}.png")
    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    log.info("Chart-IMG saved: %s (%s)", out_path, label)
    return out_path


def render(snapshot: Dict) -> Optional[str]:
    """Render the TradingView chart via Chart-IMG. Returns the PNG path, or None.

    If CHARTIMG_LAYOUT_ID is set, render YOUR saved TradingView layout (your own
    indicators/drawings/style); otherwise render a generic advanced chart with our
    EMA studies + level lines.
    """
    if not config.chartimg_api_key:
        log.warning("CHARTIMG_API_KEY not set — chart skipped (text plan still sent)")
        return None
    if config.chartimg_layout_id:
        log.info("Chart: TradingView layout %s @ %s (zoomOut=%s moveLeft=%s, 2 Asian sessions)",
                 config.chartimg_layout_id, _interval(),
                 config.chartimg_zoom_out, config.chartimg_move_left)
        return _render_layout()
    if config.intraday_tf != "5min":
        log.warning("INTRADAY_TF=%s — member chart spec is M5; set INTRADAY_TF=5min", config.intraday_tf)
    return _render_advanced(snapshot)


def _render_layout() -> Optional[str]:
    """Render the user's saved TradingView layout via Chart-IMG layout-chart."""
    url = f"https://api.chart-img.com/v2/tradingview/layout-chart/{config.chartimg_layout_id}"
    headers = {"x-api-key": config.chartimg_api_key, "content-type": "application/json"}
    # Private layouts / invite-only indicators need the TradingView session cookies.
    if config.chartimg_tv_session:
        headers["tradingview-session-id"] = config.chartimg_tv_session
    if config.chartimg_tv_session_sign:
        headers["tradingview-session-id-sign"] = config.chartimg_tv_session_sign
    # The layout carries its own studies/drawings; we only set symbol/interval/size.
    payload = {
        "symbol": config.chartimg_symbol,
        "interval": _interval(),
        "width": config.chartimg_width,
        "height": config.chartimg_height,
    }
    if config.chartimg_zoom_out > 0:
        payload["zoomOut"] = min(config.chartimg_zoom_out, 25)
    if config.chartimg_move_left > 0:
        payload["moveLeft"] = min(config.chartimg_move_left, 50)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        log.warning("Chart-IMG layout request failed: %s", exc)
        return None
    return _save_png(resp, f"layout {config.chartimg_layout_id} {_interval()}")


def _render_advanced(snapshot: Dict) -> Optional[str]:
    """Generic advanced chart: our EMA studies + key level lines."""
    # Chart-IMG counts studies + drawings against one cap (free tier = 3). Prioritise
    # the EMAs (core indicators; S/R levels are also listed in the text plan), then fill
    # remaining budget with level lines.
    studies = [
        {"name": "Moving Average Exponential", "input": {"length": 20}},
        {"name": "Moving Average Exponential", "input": {"length": 50}},
        {"name": "Moving Average Exponential", "input": {"length": 200}},
    ]
    budget = max(0, config.chartimg_max_params)
    studies = studies[:budget]
    drawings = _drawings(snapshot)[:max(0, budget - len(studies))]

    payload = {
        "symbol": config.chartimg_symbol,
        "interval": _interval(),
        "theme": "dark",
        "width": config.chartimg_width,
        "height": config.chartimg_height,
        "studies": studies,
        "drawings": drawings,
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
    return _save_png(resp, f"{config.chartimg_symbol} {_interval()}, {len(drawings)} levels")
