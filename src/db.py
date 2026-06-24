"""Database layer — MySQL (PyMySQL).

Persists economic events and intraday analyses, and tracks which alerts have been
sent (sent_outlook / sent_alert_60 / sent_alert_15 / sent_post_release) so a 1-minute
cron tick can run idempotently and never double-post.

Connections are opened per call — appropriate for short cron-driven jobs.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

import pymysql
import pymysql.cursors
import pytz

from config import config

log = logging.getLogger("db")

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")

# cPanel MySQL users are granted on @localhost, which only matches a unix-socket
# connection. PyMySQL otherwise connects over TCP (::1) and is denied. So when the
# host is local, prefer the socket (explicit DB_SOCKET, else the common paths).
_SOCKET_CANDIDATES = [
    "/var/lib/mysql/mysql.sock",
    "/var/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
]


def _detect_socket() -> Optional[str]:
    if config.db.socket:
        return config.db.socket
    if config.db.host in ("localhost", "127.0.0.1", "::1"):
        for path in _SOCKET_CANDIDATES:
            if os.path.exists(path):
                return path
    return None


@contextmanager
def get_conn():
    kwargs = dict(
        user=config.db.user,
        password=config.db.password,
        database=config.db.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
    )
    sock = _detect_socket()
    if sock:
        kwargs["unix_socket"] = sock
    else:
        kwargs["host"] = config.db.host
        kwargs["port"] = config.db.port
    conn = pymysql.connect(**kwargs)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """Create tables from schema.sql (idempotent — uses IF NOT EXISTS)."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        sql = fh.read()
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            _migrate_intraday_columns(cur)
    log.info("Schema ensured (%d statements)", len(statements))


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (config.db.name, table, column),
    )
    return bool(cur.fetchone()["n"])


def _migrate_intraday_columns(cur) -> None:
    """Align intraday_analyses with schema.sql on existing DBs (renames + missing cols)."""
    renames = (
        ("raw_market_data_json", "market_data_json"),
        ("plan_json", "gpt_output_json"),
        ("member_message", "telegram_message"),
    )
    for old, new in renames:
        if _column_exists(cur, "intraday_analyses", old) and not _column_exists(cur, "intraday_analyses", new):
            cur.execute(
                f"ALTER TABLE intraday_analyses CHANGE `{old}` `{new}` LONGTEXT DEFAULT NULL"
            )
            log.info("Migrated intraday_analyses.%s -> %s", old, new)

    adds = (
        ("chart_path", "VARCHAR(255) DEFAULT NULL"),
        ("market_data_json", "LONGTEXT DEFAULT NULL"),
        ("bias", "VARCHAR(16) DEFAULT NULL"),
        ("market_condition", "VARCHAR(24) DEFAULT NULL"),
        ("confidence", "TINYINT UNSIGNED DEFAULT NULL"),
        ("gpt_output_json", "LONGTEXT DEFAULT NULL"),
        ("telegram_message", "LONGTEXT DEFAULT NULL"),
        ("timeframe", "VARCHAR(8) NOT NULL DEFAULT 'M5'"),
    )
    for col, typedef in adds:
        if not _column_exists(cur, "intraday_analyses", col):
            cur.execute(f"ALTER TABLE intraday_analyses ADD COLUMN `{col}` {typedef}")
            log.info("Added intraday_analyses.%s", col)


_intraday_schema_checked = False


def _ensure_intraday_schema() -> None:
    """Run intraday column migrations once per process (cheap no-op after first call)."""
    global _intraday_schema_checked
    if _intraday_schema_checked:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            _migrate_intraday_columns(cur)
    _intraday_schema_checked = True


def _naive_utc(dt: datetime) -> str:
    return dt.astimezone(pytz.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _naive_sgt(dt: datetime) -> str:
    return dt.astimezone(config.tz).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# economic_events
# ---------------------------------------------------------------------------

def upsert_event(event) -> None:
    """Insert or update an event (keyed on source + source_event_id).

    Updates forecast/previous/actual/impact/schedule/category on conflict but
    PRESERVES the sent_* flags so re-fetching the calendar never re-sends alerts.
    """
    sql = """
        INSERT INTO economic_events
            (source, source_event_id, currency, country, event_name, impact,
             scheduled_at_utc, scheduled_at_sgt, forecast, previous, actual,
             unit, category, status)
        VALUES
            (%(source)s, %(seid)s, %(currency)s, %(country)s, %(event_name)s, %(impact)s,
             %(utc)s, %(sgt)s, %(forecast)s, %(previous)s, %(actual)s,
             %(unit)s, %(category)s, 'scheduled')
        ON DUPLICATE KEY UPDATE
            impact=VALUES(impact), scheduled_at_utc=VALUES(scheduled_at_utc),
            scheduled_at_sgt=VALUES(scheduled_at_sgt), forecast=VALUES(forecast),
            previous=VALUES(previous),
            actual=COALESCE(VALUES(actual), actual),
            category=VALUES(category), updated_at=CURRENT_TIMESTAMP
    """
    params = {
        "source": event.source, "seid": event.source_event_id,
        "currency": event.currency, "country": event.country,
        "event_name": event.event_name, "impact": event.impact,
        "utc": _naive_utc(event.scheduled_utc), "sgt": _naive_sgt(event.scheduled_sgt),
        "forecast": event.forecast, "previous": event.previous, "actual": event.actual,
        "unit": event.unit, "category": event.category,
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def fetch_for_alert(field: str, from_utc: datetime, to_utc: datetime) -> List[Dict]:
    """High/medium events scheduled in [from_utc, to_utc] whose `field` flag is 0."""
    assert field in {"sent_outlook", "sent_alert_60", "sent_alert_15", "sent_post_release"}
    sql = f"""
        SELECT * FROM economic_events
        WHERE impact IN ('high','medium')
          AND status = 'scheduled'
          AND scheduled_at_utc BETWEEN %s AND %s
          AND {field} = 0
        ORDER BY scheduled_at_utc
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (_naive_utc(from_utc), _naive_utc(to_utc)))
            return cur.fetchall()


def fetch_for_postrelease(now_utc: datetime, max_age_minutes: int = 20) -> List[Dict]:
    """Events whose release time has passed (within max_age) still needing a post-release."""
    sql = """
        SELECT * FROM economic_events
        WHERE impact IN ('high','medium')
          AND status = 'scheduled'
          AND sent_post_release = 0
          AND scheduled_at_utc <= %s
          AND scheduled_at_utc >= %s
        ORDER BY scheduled_at_utc
    """
    upper = _naive_utc(now_utc)
    lower = _naive_utc(now_utc - _timedelta_minutes(max_age_minutes))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (upper, lower))
            return cur.fetchall()


def mark_sent(event_id: int, field: str) -> None:
    assert field in {"sent_outlook", "sent_alert_60", "sent_alert_15", "sent_post_release"}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE economic_events SET {field}=1 WHERE id=%s", (event_id,))


def suppress_event(event_id: int) -> None:
    """Stop all alerts for a row that should not be tracked (stale/irrelevant)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE economic_events SET status='ignored', "
                "sent_alert_60=1, sent_alert_15=1, sent_post_release=1 "
                "WHERE id=%s",
                (event_id,),
            )


def suppress_non_relevant_scheduled() -> int:
    """Mark future scheduled rows ignored when they fail the XAUUSD filter or are legacy FMP."""
    import news_filter

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, source, event_name FROM economic_events "
                "WHERE status='scheduled' AND scheduled_at_utc > UTC_TIMESTAMP()"
            )
            rows = cur.fetchall()
            suppressed = 0
            for row in rows:
                legacy_fmp = (row.get("source") or "").lower() == "fmp"
                if legacy_fmp or not news_filter.is_xauusd_relevant(row["event_name"]):
                    cur.execute(
                        "UPDATE economic_events SET status='ignored', "
                        "sent_alert_60=1, sent_alert_15=1, sent_post_release=1 "
                        "WHERE id=%s",
                        (row["id"],),
                    )
                    suppressed += 1
    if suppressed:
        log.info("Suppressed %d non-relevant/stale scheduled event(s)", suppressed)
    return suppressed


def update_actual(event_id: int, actual: Optional[str], polarity: Optional[str], status: str = "released") -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE economic_events SET actual=%s, polarity=%s, status=%s, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (actual, polarity, status, event_id),
            )


# ---------------------------------------------------------------------------
# intraday_analyses & audit
# ---------------------------------------------------------------------------

def insert_intraday(row: Dict) -> int:
    _ensure_intraday_schema()
    cols = ["instrument", "analysis_time_utc", "analysis_time_sgt", "timeframe",
            "chart_path", "market_data_json", "bias", "market_condition", "confidence",
            "gpt_output_json", "telegram_message"]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    sql = f"INSERT INTO intraday_analyses ({', '.join(cols)}) VALUES ({placeholders})"
    params = {c: row.get(c) for c in cols}
    chart = params.get("chart_path")
    if chart and len(str(chart)) > 255:
        params["chart_path"] = str(chart)[-255:]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid


def audit(module: str, action: str, input_json: str = None,
          output_json: str = None, error_message: str = None) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bot_audit_logs (module, action, input_json, output_json, error_message) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (module, action, input_json, output_json, error_message),
                )
    except Exception as exc:  # auditing must never break the main flow
        log.warning("audit write failed: %s", exc)


def _timedelta_minutes(minutes: int):
    from datetime import timedelta
    return timedelta(minutes=minutes)
