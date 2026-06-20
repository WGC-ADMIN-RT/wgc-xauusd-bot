"""Chart Renderer — 200-candle M15 XAU/USD chart with EMAs and key levels.

Default provider is "self" (free, rendered with mplfinance). A "chartimg" provider
(TradingView via Chart-IMG) can be slotted in later behind the same `render()` call.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, Optional

import matplotlib
matplotlib.use("Agg")  # headless server rendering
import mplfinance as mpf
import pandas as pd

from config import config

log = logging.getLogger("charts")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "charts_out")

_LEVEL_STYLE = {
    "previous_day.high": ("#c0392b", "Prev Day High"),
    "previous_day.low": ("#27ae60", "Prev Day Low"),
    "today.asian_session_high": ("#e67e22", "Asian High"),
    "today.asian_session_low": ("#2980b9", "Asian Low"),
    "today.open": ("#7f8c8d", "Day Open"),
}


def _dig(snapshot: Dict, dotted: str):
    cur = snapshot
    for part in dotted.split("."):
        cur = (cur or {}).get(part)
    return cur


def render(snapshot: Dict) -> Optional[str]:
    """Render the chart from a market_data snapshot. Returns the PNG path (or None)."""
    if config.chart_provider == "chartimg":
        return _render_chartimg(snapshot)
    return _render_self(snapshot)


def _render_self(snapshot: Dict) -> Optional[str]:
    df = snapshot.get("_m15_df")
    if df is None or len(df) == 0:
        log.error("No M15 dataframe in snapshot — cannot render chart")
        return None

    plot_df = df[["open", "high", "low", "close", "volume"]].copy()
    plot_df.columns = ["Open", "High", "Low", "Close", "Volume"]
    plot_df.index = pd.DatetimeIndex(plot_df.index).tz_localize(None)  # mplfinance wants tz-naive

    close = plot_df["Close"]
    addplots = [
        mpf.make_addplot(close.ewm(span=20, adjust=False).mean(), color="#2980b9", width=1.0),
        mpf.make_addplot(close.ewm(span=50, adjust=False).mean(), color="#e67e22", width=1.0),
    ]
    if len(plot_df) >= 200:
        addplots.append(mpf.make_addplot(close.ewm(span=200, adjust=False).mean(), color="#8e44ad", width=1.2))

    hlines, colors = [], []
    for key, (color, _label) in _LEVEL_STYLE.items():
        val = _dig(snapshot, key)
        if val is not None:
            hlines.append(val)
            colors.append(color)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now(config.tz).strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUTPUT_DIR, f"{config.instrument}_M15_{stamp}.png")

    title = f"\n{config.instrument}  M15 — last {len(plot_df)} candles  ({stamp} SGT)"
    style = mpf.make_mpf_style(base_mpf_style="charles", rc={"font.size": 9})

    try:
        mpf.plot(
            plot_df,
            type="candle",
            style=style,
            addplot=addplots,
            hlines=dict(hlines=hlines, colors=colors, linestyle="--", linewidths=0.9) if hlines else None,
            volume=True,
            figratio=(16, 9),
            figscale=1.2,
            tight_layout=True,
            title=title,
            savefig=dict(fname=out_path, dpi=130, bbox_inches="tight"),
        )
    except Exception as exc:  # rendering must never crash the analysis job
        log.exception("Chart render failed: %s", exc)
        return None

    log.info("Chart saved: %s (EMA20/50%s + %d levels)",
             out_path, "/200" if len(plot_df) >= 200 else "", len(hlines))
    return out_path


def _render_chartimg(snapshot: Dict) -> Optional[str]:
    """Placeholder for TradingView screenshots via Chart-IMG (paid). Wired later."""
    log.warning("CHART_PROVIDER=chartimg not yet implemented — falling back to self-render")
    return _render_self(snapshot)
