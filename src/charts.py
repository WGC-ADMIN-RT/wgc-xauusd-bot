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
from typing import Dict, List, Optional, Tuple

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


def _layout_zoom_for_hours(hours: float, width: int) -> Tuple[int, int, int, int]:
    """Return ``(zoomOut, moveLeft, moveRight, zoomIn)`` for two Asian session boxes.

    Layout charts use pan/zoom only (``range`` breaks session shading on saved layouts).
    Balance: enough ``moveLeft`` for two session columns, enough ``moveRight`` to drop
    the empty future grid, moderate ``zoomIn`` for readable candles.
    """
    target_hours = min(hours, 46.0)
    baseline_hours = 10.0 * (width / 800.0)

    if target_hours <= baseline_hours * 0.95:
        return 2, 6, 14, 2

    zoom_out = max(3, min(6, round(target_hours / 9)))
    move_left = max(8, min(28, round(target_hours / 3.8)))
    move_right = max(12, min(35, round(target_hours / 5)))
    zoom_in = max(2, min(4, round(28 / target_hours)))
    return zoom_out, move_left, move_right, zoom_in


def _chart_range_hours(chart_range: Dict) -> Optional[float]:
    try:
        start = datetime.fromisoformat(chart_range["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(chart_range["to"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (end - start).total_seconds() / 3600)


def _apply_chart_view(payload: Dict, snapshot: Dict, *, layout: bool) -> None:
    """Pin the screenshot to two Asian sessions (08:00–16:00 SGT)."""
    chart_range = snapshot.get("chart_range")
    if not chart_range:
        return

    if not layout:
        payload["range"] = chart_range
        payload["timezone"] = config.timezone_name
        return

    # Layout charts: pan/zoom only — sending ``range`` hides session shading on layouts.
    manual = any(
        getattr(config, f"chartimg_{k}", 0) > 0
        for k in ("zoom_in", "zoom_out", "move_left", "move_right")
    )
    if manual:
        if config.chartimg_zoom_in > 0:
            payload["zoomIn"] = min(config.chartimg_zoom_in, 25)
        if config.chartimg_zoom_out > 0:
            payload["zoomOut"] = min(config.chartimg_zoom_out, 25)
        if config.chartimg_move_left > 0:
            payload["moveLeft"] = min(config.chartimg_move_left, 50)
        if config.chartimg_move_right > 0:
            payload["moveRight"] = min(config.chartimg_move_right, 50)
        payload["resetZoom"] = True
        return

    hours = _chart_range_hours(chart_range)
    if hours is None:
        return
    zoom_out, move_left, move_right, zoom_in = _layout_zoom_for_hours(
        hours, config.chartimg_width,
    )
    payload["resetZoom"] = True
    payload["zoomOut"] = zoom_out
    payload["moveLeft"] = move_left
    payload["moveRight"] = move_right
    payload["zoomIn"] = zoom_in
    log.info(
        "Chart layout view: %.1fh -> zoomOut=%s moveLeft=%s moveRight=%s zoomIn=%s",
        hours, zoom_out, move_left, move_right, zoom_in,
    )


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
        log.info("Chart: TradingView layout %s @ %s (2 Asian sessions)",
                 config.chartimg_layout_id, _interval())
        return _render_layout(snapshot)
    if config.intraday_tf != "5min":
        log.warning("INTRADAY_TF=%s — member chart spec is M5; set INTRADAY_TF=5min", config.intraday_tf)
    return _render_advanced(snapshot)


def _layout_chart_payload(snapshot: Dict) -> Dict:
    """Chart-IMG layout request body — symbol/interval/size + two-session view."""
    payload = {
        "symbol": config.chartimg_symbol,
        "interval": _interval(),
        "width": config.chartimg_width,
        "height": config.chartimg_height,
    }
    _apply_chart_view(payload, snapshot, layout=True)
    return payload


def _render_layout(snapshot: Dict) -> Optional[str]:
    """Render the user's saved TradingView layout via Chart-IMG layout-chart."""
    url = f"https://api.chart-img.com/v2/tradingview/layout-chart/{config.chartimg_layout_id}"
    headers = {"x-api-key": config.chartimg_api_key, "content-type": "application/json"}
    # Private layouts / invite-only indicators need the TradingView session cookies.
    if config.chartimg_tv_session:
        headers["tradingview-session-id"] = config.chartimg_tv_session
    if config.chartimg_tv_session_sign:
        headers["tradingview-session-id-sign"] = config.chartimg_tv_session_sign
    # The layout carries its own studies/drawings; we only set symbol/interval/size.
    payload = _layout_chart_payload(snapshot)
    log.info(
        "Chart-IMG layout %s payload symbol=%s interval=%s tv_session=%s",
        config.chartimg_layout_id,
        payload.get("symbol"),
        payload.get("interval"),
        "yes" if config.chartimg_tv_session else "no",
    )
    if config.chartimg_layout_id and not config.chartimg_tv_session:
        log.warning(
            "CHARTIMG_TV_SESSION_ID not set — Chart-IMG may use the layout's saved "
            "symbol (e.g. FOREX.com) instead of CHARTIMG_SYMBOL=%s",
            config.chartimg_symbol,
        )
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
    _apply_chart_view(payload, snapshot, layout=False)
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
