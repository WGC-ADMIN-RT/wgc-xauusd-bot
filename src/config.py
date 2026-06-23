"""Central configuration. All secrets/settings come from environment variables.

Load order on the server: a `.env` file (via the cron wrapper) populates the
environment, then this module reads it. Nothing here is ever committed with real
values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

import pytz


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DBConfig:
    host: str = field(default_factory=lambda: _get("DB_HOST", "localhost"))
    name: str = field(default_factory=lambda: _get("DB_NAME"))
    user: str = field(default_factory=lambda: _get("DB_USER"))
    password: str = field(default_factory=lambda: _get("DB_PASSWORD"))
    port: int = field(default_factory=lambda: _get_int("DB_PORT", 3306))
    socket: str = field(default_factory=lambda: _get("DB_SOCKET"))  # cPanel grants @localhost -> use socket


@dataclass(frozen=True)
class Config:
    # Secrets / services
    fmp_api_key: str = field(default_factory=lambda: _get("FMP_API_KEY"))
    telegram_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    wgc_bots_chat_id: str = field(default_factory=lambda: _get("TELEGRAM_WGC_BOTS_CHAT_ID"))
    public_chat_id: str = field(default_factory=lambda: _get("TELEGRAM_PUBLIC_CHAT_ID"))

    # Routing: "wgc_bots" (test/internal) or "public" (members)
    publish_target: str = field(default_factory=lambda: _get("PUBLISH_TARGET", "wgc_bots"))

    # Charts: rendered by Chart-IMG (TradingView). Needs CHARTIMG_API_KEY; without it
    # the intraday job still publishes the text plan (chart marked unavailable).
    chart_provider: str = field(default_factory=lambda: _get("CHART_PROVIDER", "chartimg"))
    chartimg_api_key: str = field(default_factory=lambda: _get("CHARTIMG_API_KEY"))
    chartimg_symbol: str = field(default_factory=lambda: _get("CHARTIMG_SYMBOL", "OANDA:XAUUSD"))
    chartimg_width: int = field(default_factory=lambda: _get_int("CHARTIMG_WIDTH", 800))
    chartimg_height: int = field(default_factory=lambda: _get_int("CHARTIMG_HEIGHT", 600))
    # Chart-IMG free tier caps total studies+drawings at 3 (and resolution at 800x600).
    # Raise via env if you upgrade the plan (e.g. 8 -> 3 EMAs + 5 level lines).
    chartimg_max_params: int = field(default_factory=lambda: _get_int("CHARTIMG_MAX_PARAMS", 3))
    # Render from YOUR TradingView account's saved layout (your indicators/drawings/style)
    # via Chart-IMG layout-chart. Set the layout id from the chart's share URL. For a
    # PRIVATE layout / invite-only indicators, also supply your TradingView session
    # cookies (sensitive — server .env only, never commit, rotate if exposed).
    chartimg_layout_id: str = field(default_factory=lambda: _get("CHARTIMG_LAYOUT_ID"))
    chartimg_tv_session: str = field(default_factory=lambda: _get("CHARTIMG_TV_SESSION_ID"))
    chartimg_tv_session_sign: str = field(default_factory=lambda: _get("CHARTIMG_TV_SESSION_SIGN"))

    # Operational
    timezone_name: str = field(default_factory=lambda: _get("TIMEZONE", "Asia/Singapore"))
    instrument: str = field(default_factory=lambda: _get("INSTRUMENT", "XAUUSD"))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))

    # Intraday analysis timeframe (FMP slug). Client wants M5 -> "5min".
    intraday_tf: str = field(default_factory=lambda: _get("INTRADAY_TF", "5min"))

    # Intraday AI analysis — Claude as a 20-yr XAUUSD trader (M5 execution, H1 bias),
    # emitting key zones as ranges + 4-5 game plans. When enabled AND ANTHROPIC_API_KEY
    # is set, the 2:30 PM plan is AI-generated via tool-use; otherwise (disabled,
    # unkeyed, or the call fails) it falls back to the deterministic rule-based plan.
    anthropic_api_key: str = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    intraday_ai_enabled: bool = field(
        default_factory=lambda: _get("INTRADAY_AI_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"))
    intraday_ai_model: str = field(
        default_factory=lambda: _get("INTRADAY_AI_MODEL", "claude-opus-4-8"))
    intraday_gameplans: int = field(default_factory=lambda: _get_int("INTRADAY_GAMEPLANS", 5))

    # News filter (spec: USD, high + medium impact only)
    news_currency: str = "USD"
    news_countries: tuple = ("United States",)
    news_impacts: tuple = ("high", "medium")

    # Schedules (SGT)
    daily_outlook_sgt: str = "12:00"
    intraday_analysis_sgt: str = "14:30"
    pre_alert_minutes: int = 60
    final_warning_minutes: int = 15

    db: DBConfig = field(default_factory=DBConfig)

    @property
    def tz(self):
        return pytz.timezone(self.timezone_name)

    @property
    def intraday_tf_label(self) -> str:
        return {"1min": "M1", "5min": "M5", "15min": "M15", "30min": "M30",
                "1hour": "H1", "4hour": "H4"}.get(self.intraday_tf, self.intraday_tf.upper())

    @property
    def target_chat_id(self) -> str:
        return self.public_chat_id if self.publish_target == "public" else self.wgc_bots_chat_id

    def validate(self) -> List[str]:
        """Return a list of missing-but-required settings (empty == OK)."""
        problems = []
        if not self.fmp_api_key:
            problems.append("FMP_API_KEY is not set")
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set")
        if not self.db.name:
            problems.append("DB_NAME is not set")
        if not self.db.user:
            problems.append("DB_USER is not set")
        if not self.db.password:
            problems.append("DB_PASSWORD is not set")
        if not self.target_chat_id:
            problems.append(f"No chat id for publish target '{self.publish_target}'")
        if self.chart_provider == "chartimg" and not self.chartimg_api_key:
            problems.append("CHART_PROVIDER=chartimg but CHARTIMG_API_KEY is not set")
        return problems


config = Config()
