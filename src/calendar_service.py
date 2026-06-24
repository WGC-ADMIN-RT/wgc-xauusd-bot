"""Economic Calendar Service — Forex Factory (primary) + FMP (actual fallback).

Member-facing event lists come from Forex Factory's official weekly JSON export so
the outlook matches FF red/orange USD folders exactly. FMP is only used as a
fallback when polling for the released *actual* after an event if FF has not
updated yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config
import polarity
import news_filter
import forex_factory

log = logging.getLogger("calendar")

FMP_BASE = "https://financialmodelingprep.com"
ECON_ENDPOINT = "/stable/economic-calendar"

# FMP calendar timestamps are treated as this zone, then converted to UTC/SGT.
FMP_CALENDAR_TZ = pytz.UTC

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "wgc-xauusd-bot/1.0"})


class CalendarError(Exception):
    pass


@dataclass
class Event:
    source: str
    source_event_id: str
    currency: str
    country: str
    event_name: str
    impact: str                       # high | medium | low
    scheduled_utc: datetime
    scheduled_sgt: datetime
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    unit: Optional[str] = None
    category: str = "unknown"
    is_major: bool = False

    def template_dict(self) -> Dict:
        """Shape expected by templates.daily_outlook / alert_1h / warning_15m."""
        return {
            "time_sgt": _fmt_time_sgt(self.scheduled_sgt),
            "impact": self.impact,
            "event_name": news_filter.display_name(self.event_name),
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
            "short_reason": polarity.pre_release_note(self.event_name),
        }


def _fmt_time_sgt(dt: datetime) -> str:
    """'8:30 PM' — cross-platform (no %-I)."""
    return dt.strftime("%I:%M %p").lstrip("0")


def _normalize_impact(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in {"high", "medium", "low"}:
        return s
    # Some feeds use numeric/star ratings — map defensively.
    if s in {"3", "***"}:
        return "high"
    if s in {"2", "**"}:
        return "medium"
    if s in {"1", "*"}:
        return "low"
    return "low"


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s and s.lower() not in {"none", "null", "nan"} else None


@retry(
    retry=retry_if_exception_type((requests.RequestException, CalendarError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=True,
)
def _request(from_date: str, to_date: str) -> List[dict]:
    if not config.fmp_api_key:
        raise CalendarError("FMP_API_KEY not configured")
    params = {"from": from_date, "to": to_date, "apikey": config.fmp_api_key}
    url = f"{FMP_BASE}{ECON_ENDPOINT}"
    resp = _SESSION.get(url, params=params, timeout=30)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise CalendarError(f"FMP transient error {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("Error Message"):
        raise CalendarError(f"FMP: {data['Error Message']}")
    if not isinstance(data, list):
        raise CalendarError(f"Unexpected FMP payload type: {type(data).__name__}")
    return data


def _to_utc_sgt(raw_date: str):
    """Parse FMP 'YYYY-MM-DD HH:MM:SS' -> (utc, sgt)."""
    dt = datetime.strptime(raw_date.strip(), "%Y-%m-%d %H:%M:%S")
    aware = FMP_CALENDAR_TZ.localize(dt)
    return aware.astimezone(pytz.UTC), aware.astimezone(config.tz)


def normalize(raw: dict) -> Optional[Event]:
    """Map one FMP record -> Event, or None if it fails the USD high/medium filter."""
    currency = (raw.get("currency") or "").upper()
    if currency != config.news_currency:           # USD only
        return None

    impact = _normalize_impact(raw.get("impact"))
    if impact not in config.news_impacts:           # high + medium only
        return None

    raw_date = raw.get("date")
    if not raw_date:
        return None
    try:
        utc, sgt = _to_utc_sgt(raw_date)
    except (ValueError, TypeError) as exc:
        log.warning("Bad event date %r: %s", raw_date, exc)
        return None

    name = (raw.get("event") or "").strip()
    forecast = _clean(raw.get("estimate") if raw.get("estimate") is not None else raw.get("forecast"))
    previous = _clean(raw.get("previous"))
    actual = _clean(raw.get("actual"))
    category, _ = polarity.classify_event(name)

    return Event(
        source="fmp",
        source_event_id=f"fmp|{raw_date}|{name}|{currency}",
        currency=currency,
        country=(raw.get("country") or "US"),
        event_name=name,
        impact=impact,
        scheduled_utc=utc,
        scheduled_sgt=sgt,
        forecast=forecast,
        previous=previous,
        actual=actual,
        unit=_clean(raw.get("unit")),
        category=category,
        is_major=polarity.is_major_event(name),
    )


def fetch_usd_events(from_date: str, to_date: str) -> List[Event]:
    """Fetch USD high/medium events from Forex Factory for the inclusive date range."""
    return forex_factory.fetch_usd_events(from_date, to_date)


def outlook_window(now_sgt: datetime):
    """24h forward window: today 12:00 PM SGT -> tomorrow 11:59 AM SGT."""
    start = now_sgt.replace(hour=12, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1)).replace(hour=11, minute=59, second=59)
    return start, end


def get_outlook(now_sgt: Optional[datetime] = None) -> List[Event]:
    """USD high/medium events inside the 24h forward window (for the 12 PM outlook)."""
    now_sgt = now_sgt or datetime.now(config.tz)
    start, end = outlook_window(now_sgt)
    # Query a slightly wider date range, then trim to the exact SGT window.
    from_date = start.astimezone(config.tz).strftime("%Y-%m-%d")
    to_date = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    events = fetch_usd_events(from_date, to_date)
    return [e for e in events if start <= e.scheduled_sgt <= end]


def get_tracked_events(
    now_sgt: Optional[datetime] = None,
    days_forward: int = 7,
    days_back: int = 1,
) -> List[Event]:
    """All XAUUSD-relevant FF events for DB sync (wider than the 24h outlook message)."""
    now_sgt = now_sgt or datetime.now(config.tz)
    start = now_sgt.date() - timedelta(days=days_back)
    end = now_sgt.date() + timedelta(days=days_forward)
    return fetch_usd_events(start.isoformat(), end.isoformat())


def sync_to_db(now_sgt: Optional[datetime] = None) -> int:
    """Upsert tracked FF events so 1h/15m alerts can fire. Returns count upserted."""
    import db

    events = get_tracked_events(now_sgt)
    for e in events:
        db.upsert_event(e)
    db.suppress_non_relevant_scheduled()
    log.info("Calendar sync: upserted %d tracked event(s)", len(events))
    return len(events)


def group_by_time(events: List[Event]) -> List[List[Event]]:
    """Group events that release at the same SGT minute (spec: one alert per group)."""
    groups: Dict[str, List[Event]] = {}
    for e in events:
        key = e.scheduled_sgt.strftime("%Y-%m-%d %H:%M")
        groups.setdefault(key, []).append(e)
    return [groups[k] for k in sorted(groups.keys())]


def fetch_actual(event_name: str, scheduled_utc: datetime) -> Optional[str]:
    """Return the released actual from FF, falling back to a same-day FMP pull."""
    actual = forex_factory.fetch_actual(event_name, scheduled_utc)
    if actual:
        return actual
    date = scheduled_utc.astimezone(pytz.UTC).strftime("%Y-%m-%d")
    try:
        raw = _request(date, date)
    except CalendarError as exc:
        log.warning("fetch_actual FMP fallback failed for %s: %s", event_name, exc)
        return None
    target = event_name.strip().lower()
    for r in raw:
        if (r.get("event") or "").strip().lower() == target:
            return _clean(r.get("actual"))
    return None
