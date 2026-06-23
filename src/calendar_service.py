"""Economic Calendar Service — Financial Modeling Prep (stable API).

Pulls USD high/medium-impact events with forecast / previous / actual, normalises
them to UTC + SGT, and applies the deterministic polarity classification.

Notes
-----
* This account's key is NOT a legacy user, so the deprecated `/api/v3/economic_calendar`
  endpoint is blocked — we use the current `/stable/economic-calendar` endpoint.
* FMP returns the event `date` as a naive timestamp. Empirically this is **UTC**; it is
  made configurable via FMP_CALENDAR_TZ so we can flip it in one place if the first live
  pull shows otherwise (verify against a known release time on day one).
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
    """Fetch and filter to USD high/medium events for the inclusive date range."""
    raw = _request(from_date, to_date)
    normalized = [e for e in (normalize(r) for r in raw) if e is not None]
    events = news_filter.apply(normalized)          # FF-style: drop energy/auction/derived + composite PMI
    events.sort(key=lambda e: e.scheduled_utc)
    log.info("Fetched %d raw -> %d USD high/medium -> %d after FF filter (%s..%s)",
             len(raw), len(normalized), len(events), from_date, to_date)
    return events


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


def group_by_time(events: List[Event]) -> List[List[Event]]:
    """Group events that release at the same SGT minute (spec: one alert per group)."""
    groups: Dict[str, List[Event]] = {}
    for e in events:
        key = e.scheduled_sgt.strftime("%Y-%m-%d %H:%M")
        groups.setdefault(key, []).append(e)
    return [groups[k] for k in sorted(groups.keys())]


def fetch_actual(event_name: str, scheduled_utc: datetime) -> Optional[str]:
    """Re-fetch the event's day and return its 'actual' value once released (else None)."""
    date = scheduled_utc.astimezone(pytz.UTC).strftime("%Y-%m-%d")
    try:
        raw = _request(date, date)
    except CalendarError as exc:
        log.warning("fetch_actual failed for %s: %s", event_name, exc)
        return None
    target = event_name.strip().lower()
    for r in raw:
        if (r.get("event") or "").strip().lower() == target:
            return _clean(r.get("actual"))
    return None
