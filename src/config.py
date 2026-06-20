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
    name: str = field(default_factory=lambda: _get("DB_NAME", "upayztec_wgcxau"))
    user: str = field(default_factory=lambda: _get("DB_USER", "upayztec_wgcxau"))
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

    # Charts: "self" (free) or "chartimg" (TradingView via Chart-IMG)
    chart_provider: str = field(default_factory=lambda: _get("CHART_PROVIDER", "self"))
    chartimg_api_key: str = field(default_factory=lambda: _get("CHARTIMG_API_KEY"))

    # Operational
    timezone_name: str = field(default_factory=lambda: _get("TIMEZONE", "Asia/Singapore"))
    instrument: str = field(default_factory=lambda: _get("INSTRUMENT", "XAUUSD"))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))

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
    def target_chat_id(self) -> str:
        return self.public_chat_id if self.publish_target == "public" else self.wgc_bots_chat_id

    def validate(self) -> List[str]:
        """Return a list of missing-but-required settings (empty == OK)."""
        problems = []
        if not self.fmp_api_key:
            problems.append("FMP_API_KEY is not set")
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set")
        if not self.db.password:
            problems.append("DB_PASSWORD is not set")
        if not self.target_chat_id:
            problems.append(f"No chat id for publish target '{self.publish_target}'")
        if self.chart_provider == "chartimg" and not self.chartimg_api_key:
            problems.append("CHART_PROVIDER=chartimg but CHARTIMG_API_KEY is not set")
        return problems


config = Config()
