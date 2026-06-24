#!/usr/bin/env python
"""Pre-flight check — cron readiness, calendar sync, stale cleanup.

Run before trusting live alerts:
    .venv/bin/python jobs/preflight.py
    .venv/bin/python jobs/preflight.py --fix   # sync calendar + suppress stale rows
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import calendar_service  # noqa: E402
import config as cfg_mod  # noqa: E402
import db  # noqa: E402
import forex_factory  # noqa: E402
import news_filter  # noqa: E402
from config import config  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGS = os.path.join(ROOT, "logs")
CACHE = os.path.join(ROOT, "cache", "ff_calendar_thisweek.json")


def _sgt_now() -> datetime:
    return datetime.now(config.tz)


def _marker(name: str, day: str) -> str:
    return os.path.join(LOGS, f".{name}_{day}")


def _check_config() -> list[str]:
    problems = config.validate()
    return problems or ["OK"]


def _check_markers(today: str, now: datetime) -> list[str]:
    lines = []
    for name, hhmm in (("outlook", "12:00"), ("intraday", "14:30")):
        path = _marker(name, today)
        hh, mm = map(int, hhmm.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if os.path.exists(path):
            if now < target:
                lines.append(
                    f"WARN  {name} marker exists for today BEFORE {hhmm} SGT — "
                    f"scheduled send may be SKIPPED (rm {path})"
                )
            else:
                lines.append(f"OK    {name} already ran today ({path})")
        else:
            if now < target:
                lines.append(f"OK    {name} not yet run today — will fire at {hhmm} SGT")
            else:
                lines.append(
                    f"WARN  {name} window passed and no marker — cron may have missed it"
                )
    return lines


def _check_ff_cache() -> list[str]:
    if not os.path.isfile(CACHE):
        return ["WARN  FF cache missing — run: .venv/bin/python jobs/sync_calendar.py"]
    age_h = (datetime.now().timestamp() - os.path.getmtime(CACHE)) / 3600
    return [f"OK    FF cache present ({age_h:.1f}h old)"]


def _check_db() -> tuple[list[str], int, int]:
    lines = []
    stale = 0
    ff_sched = 0
    sql_stale = """
        SELECT COUNT(*) AS n FROM economic_events
        WHERE status='scheduled' AND source='fmp'
    """
    sql_ff = """
        SELECT COUNT(*) AS n FROM economic_events
        WHERE status='scheduled' AND source='forexfactory'
          AND scheduled_at_utc > UTC_TIMESTAMP()
    """
    sql_upcoming = """
        SELECT event_name, scheduled_at_sgt, impact, source, status
        FROM economic_events
        WHERE status='scheduled' AND scheduled_at_utc > UTC_TIMESTAMP()
          AND source='forexfactory'
        ORDER BY scheduled_at_utc
        LIMIT 12
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_stale)
                stale = int(cur.fetchone()["n"])
                cur.execute(sql_ff)
                ff_sched = int(cur.fetchone()["n"])
                cur.execute(sql_upcoming)
                upcoming = cur.fetchall()
        if stale:
            lines.append(f"WARN  {stale} stale FMP row(s) still scheduled — run --fix")
        else:
            lines.append("OK    no stale FMP scheduled rows")
        lines.append(f"OK    {ff_sched} forexfactory event(s) scheduled in DB")
        if upcoming:
            lines.append("      Upcoming tracked releases:")
            for r in upcoming:
                lines.append(
                    f"        • {r['scheduled_at_sgt']}  {r['event_name']}  ({r['impact']})"
                )
        else:
            lines.append("WARN  no upcoming forexfactory rows — run --fix")
    except Exception as exc:
        lines.append(f"FAIL  DB check: {exc}")
    return lines, stale, ff_sched


def _check_ff_live() -> list[str]:
    try:
        rows = forex_factory.load_raw_rows(refresh=False)
        return [f"OK    FF feed readable ({len(rows)} rows in cache/feed)"]
    except Exception as exc:
        return [f"FAIL  FF feed: {exc}"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--fix", action="store_true", help="Sync calendar + suppress stale FMP rows")
    args = p.parse_args()

    now = _sgt_now()
    today = now.strftime("%Y-%m-%d")

    print("=" * 60)
    print("WGC XAUUSD — PRE-FLIGHT CHECK")
    print(f"SGT now: {now.strftime('%A, %d %b %Y %I:%M %p')}")
    print(f"Publish: {config.publish_target} → {config.target_chat_id}")
    print("=" * 60)

    if args.fix:
        print("\n--fix: syncing calendar + suppressing stale rows...")
        try:
            n = calendar_service.sync_to_db(now)
            print(f"  Synced {n} tracked event(s)")
        except Exception as exc:
            print(f"  FAIL sync: {exc}")
        try:
            s = db.suppress_non_relevant_scheduled()
            print(f"  Suppressed {s} non-relevant/stale row(s)")
        except Exception as exc:
            print(f"  FAIL suppress: {exc}")

    sections = [
        ("Config", _check_config()),
        ("Daily markers (outlook 12:00 / intraday 14:30 SGT)", _check_markers(today, now)),
        ("Forex Factory cache", _check_ff_cache()),
        ("Forex Factory feed", _check_ff_live()),
    ]
    db_lines, stale, ff_n = _check_db()
    sections.append(("Database / calendar sync", db_lines))

    warnings = 0
    fails = 0
    for title, lines in sections:
        print(f"\n[{title}]")
        for line in lines:
            print(f"  {line}")
            if line.startswith("WARN"):
                warnings += 1
            if line.startswith("FAIL"):
                fails += 1

    print("\n" + "=" * 60)
    if fails:
        print("STATUS: NOT READY — fix FAIL items above")
    elif warnings:
        print("STATUS: MOSTLY READY — review WARN items (run --fix or rm markers)")
    else:
        print("STATUS: GOOD TO GO")
    print("=" * 60)

    print("\nCron (verify in cPanel → Cron Jobs):")
    print("  */5  * * * *  run_outlook.py")
    print("  */5  * * * *  run_intraday.py")
    print("  *    * * * *  run_news_cycle.py")

    if warnings and not args.fix:
        print("\nTip: .venv/bin/python jobs/preflight.py --fix")


if __name__ == "__main__":
    main()
