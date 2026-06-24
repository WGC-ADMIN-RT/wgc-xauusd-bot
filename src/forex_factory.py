"""Forex Factory economic calendar — official weekly JSON export.

Source: https://nfs.faireconomy.media/ff_calendar_thisweek.json
Times are ISO-8601 with offset (America/New_York). Impact levels match the FF
red/orange folders: High and Medium only are kept upstream.

FF rate-limits calendar downloads (~2 per 5 minutes across XML/JSON/ICS/CSV), so
responses are cached on disk and stale cache is used when the feed is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pytz
import requests

from config import config

log = logging.getLogger("forex_factory")

FF_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_APP_ROOT, "cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "ff_calendar_thisweek.json")
_CACHE_TTL_SECONDS = int(os.environ.get("FF_CALENDAR_CACHE_TTL", "1800"))  # 30 min
_STALE_MAX_SECONDS = int(os.environ.get("FF_CALENDAR_STALE_MAX", "86400"))  # 24 h

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "wgc-xauusd-bot/1.0"})

_IMPACT_MAP = {"high": "high", "medium": "medium", "low": "low",
               "holiday": "low", "non-economic": "low"}


class ForexFactoryError(Exception):
    pass


def _impact(raw: str) -> str:
    return _IMPACT_MAP.get((raw or "").strip().lower(), "low")


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _is_json_calendar(text: str) -> bool:
    return (text or "").lstrip().startswith("[")


def _read_cache() -> Optional[List[dict]]:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(rows: List[dict]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = _CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    os.replace(tmp, _CACHE_FILE)


def _cache_age_seconds() -> Optional[float]:
    try:
        return datetime.now(timezone.utc).timestamp() - os.path.getmtime(_CACHE_FILE)
    except OSError:
        return None


def _download(url: str) -> List[dict]:
    resp = _SESSION.get(url, timeout=30)
    if resp.status_code == 429:
        raise ForexFactoryError("FF rate limited (429)")
    if resp.status_code >= 500:
        raise ForexFactoryError(f"FF transient HTTP {resp.status_code}")
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    if not _is_json_calendar(resp.text):
        raise ForexFactoryError("FF response is not calendar JSON (rate limit or HTML page)")
    data = resp.json()
    if not isinstance(data, list):
        raise ForexFactoryError(f"Unexpected FF payload type: {type(data).__name__}")
    return data


def _load_raw_rows() -> List[dict]:
    """Return merged this-week (+ next-week when available) FF rows."""
    age = _cache_age_seconds()
    if age is not None and age < _CACHE_TTL_SECONDS:
        cached = _read_cache()
        if cached is not None:
            log.debug("FF calendar from cache (age %.0fs)", age)
            return cached

    rows: List[dict] = []
    try:
        rows = _download(FF_JSON_URL)
        try:
            nxt = _download(FF_NEXT_JSON_URL)
            if nxt:
                rows = rows + nxt
        except ForexFactoryError:
            log.debug("FF next-week feed unavailable; using this-week only")
        _write_cache(rows)
        log.info("FF calendar refreshed (%d rows)", len(rows))
        return rows
    except Exception as exc:
        cached = _read_cache()
        if cached is not None and (age is None or age < _STALE_MAX_SECONDS):
            log.warning("FF fetch failed (%s); using stale cache", exc)
            return cached
        raise ForexFactoryError(f"FF calendar unavailable: {exc}") from exc


def _parse_datetime(raw_date: str):
    """ISO-8601 with offset -> (utc, sgt)."""
    dt = datetime.fromisoformat(raw_date.strip())
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(pytz.UTC), dt.astimezone(config.tz)


def normalize_row(raw: dict):
    """Map one FF JSON row -> calendar_service.Event, or None if filtered out."""
    # Lazy import avoids circular dependency at module load.
    from calendar_service import Event

    currency = (raw.get("country") or "").upper()
    if currency != config.news_currency:
        return None

    impact = _impact(raw.get("impact"))
    if impact not in config.news_impacts:
        return None

    raw_date = raw.get("date")
    if not raw_date:
        return None
    try:
        utc, sgt = _parse_datetime(raw_date)
    except (ValueError, TypeError) as exc:
        log.warning("Bad FF date %r: %s", raw_date, exc)
        return None

    name = (raw.get("title") or "").strip()
    if not name:
        return None

    import polarity

    forecast = _clean(raw.get("forecast"))
    previous = _clean(raw.get("previous"))
    actual = _clean(raw.get("actual"))
    category, _ = polarity.classify_event(name)

    return Event(
        source="forexfactory",
        source_event_id=f"ff|{raw_date}|{name}|{currency}",
        currency=currency,
        country="US",
        event_name=name,
        impact=impact,
        scheduled_utc=utc,
        scheduled_sgt=sgt,
        forecast=forecast,
        previous=previous,
        actual=actual,
        unit=None,
        category=category,
        is_major=polarity.is_major_event(name),
    )


def fetch_usd_events(from_date: str, to_date: str) -> List:
    """USD high/medium FF events whose SGT calendar day falls in [from_date, to_date]."""
    import news_filter

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()

    rows = _load_raw_rows()
    events = []
    for raw in rows:
        ev = normalize_row(raw)
        if ev is None:
            continue
        day = ev.scheduled_sgt.date()
        if start <= day <= end:
            events.append(ev)

    events = news_filter.apply(events)
    events.sort(key=lambda e: e.scheduled_utc)
    log.info("FF calendar: %d raw -> %d USD high/medium in %s..%s",
             len(rows), len(events), from_date, to_date)
    return events


def fetch_actual(event_name: str, scheduled_utc: datetime) -> Optional[str]:
    """Return the released actual from the cached FF feed, if present."""
    target = (event_name or "").strip().lower()
    sched_utc = scheduled_utc
    if sched_utc.tzinfo is None:
        sched_utc = pytz.UTC.localize(sched_utc)

    for raw in _load_raw_rows():
        if (raw.get("title") or "").strip().lower() != target:
            continue
        raw_date = raw.get("date")
        if not raw_date:
            continue
        try:
            utc, _ = _parse_datetime(raw_date)
        except (ValueError, TypeError):
            continue
        if abs((utc - sched_utc).total_seconds()) > 120:
            continue
        return _clean(raw.get("actual"))
    return None
