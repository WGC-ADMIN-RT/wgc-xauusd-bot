#!/usr/bin/env python
"""Audit FF calendar vs what the WGC bot tracks — compare with DB history.

Shows, for each day in a range:
  * TRACKED — FF USD red/orange + passes XAUUSD filter (what the bot should use)
  * DROPPED — FF USD red/orange but filtered out (with reason)
  * DATABASE — rows stored in economic_events (incl. legacy FMP junk)

Examples:
    .venv/bin/python jobs/audit_news.py --from 2026-06-22 --to 2026-06-24
    .venv/bin/python jobs/audit_news.py --from 2026-06-22 --to 2026-06-24 --refresh
    .venv/bin/python jobs/audit_news.py --days 3
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import db  # noqa: E402
import forex_factory  # noqa: E402
import news_filter  # noqa: E402
from config import config  # noqa: E402


def _parse_args():
    p = argparse.ArgumentParser(description="Audit WGC USD news tracking for past days")
    p.add_argument("--from", dest="from_date", metavar="DATE", help="Start date YYYY-MM-DD (SGT calendar day)")
    p.add_argument("--to", dest="to_date", metavar="DATE", help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--days", type=int, default=3, help="If --from omitted: last N SGT days incl. today (default 3)")
    p.add_argument("--refresh", action="store_true", help="Bypass FF cache and re-download weekly feeds")
    p.add_argument("--no-db", action="store_true", help="Skip economic_events DB comparison")
    return p.parse_args()


def _date_range(args) -> tuple[str, str]:
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date or args.from_date, "%Y-%m-%d").date()
    else:
        end = datetime.now(config.tz).date()
        start = end - timedelta(days=max(1, args.days) - 1)
    return start.isoformat(), end.isoformat()


def _fmt_time_sgt(dt: datetime) -> str:
    return dt.strftime("%a %d %b %I:%M %p")


def _impact_icon(impact: str) -> str:
    return "RED" if impact == "high" else "ORG"


def _ff_usd_rows(raw_rows: List[dict], from_date: str, to_date: str) -> List[dict]:
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    out = []
    for raw in raw_rows:
        if (raw.get("country") or "").upper() != config.news_currency:
            continue
        if (raw.get("impact") or "").strip().lower() not in config.news_impacts:
            continue
        try:
            _, sgt = forex_factory._parse_datetime(raw["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (start <= sgt.date() <= end):
            continue
        out.append({"raw": raw, "sgt": sgt})
    out.sort(key=lambda x: x["sgt"])
    return out


def _db_rows(from_date: str, to_date: str) -> List[dict]:
    sql = """
        SELECT id, source, event_name, impact, status, scheduled_at_sgt,
               sent_alert_60, sent_alert_15, sent_post_release
        FROM economic_events
        WHERE DATE(scheduled_at_sgt) BETWEEN %s AND %s
        ORDER BY scheduled_at_sgt
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (from_date, to_date))
            return cur.fetchall()


def _print_day(day: str, ff_rows: List[dict], db_by_day: dict) -> None:
    day_ff = [r for r in ff_rows if r["sgt"].date().isoformat() == day]
    tracked = []
    dropped = []
    for item in day_ff:
        raw = item["raw"]
        name = (raw.get("title") or "").strip()
        reason = news_filter.drop_reason(name)
        impact = (raw.get("impact") or "").lower()
        row = {
            "time": _fmt_time_sgt(item["sgt"]),
            "impact": impact,
            "name": news_filter.display_name(name),
            "forecast": raw.get("forecast") or "—",
            "previous": raw.get("previous") or "—",
        }
        if reason is None:
            tracked.append(row)
        else:
            row["reason"] = reason
            dropped.append(row)

    print(f"\n{'=' * 72}")
    print(f"  {day}  (SGT calendar day)")
    print(f"{'=' * 72}")

    print("\n  BOT WOULD TRACK (FF red/orange + XAUUSD filter):")
    if tracked:
        for r in tracked:
            print(f"    [{_impact_icon(r['impact'])}] {r['time']}  {r['name']}")
            print(f"          Forecast: {r['forecast']}  |  Previous: {r['previous']}")
    else:
        print("    (none)")

    print("\n  FF SHOWED BUT BOT DROPS:")
    if dropped:
        for r in dropped:
            print(f"    [{_impact_icon(r['impact'])}] {r['time']}  {r['name']}")
            print(f"          Reason: {r['reason']}")
    else:
        print("    (none)")

    db_rows = db_by_day.get(day, [])
    print("\n  DATABASE (economic_events):")
    if db_rows:
        for r in db_rows:
            rel = "TRACK" if news_filter.is_xauusd_relevant(r["event_name"]) and r["source"] == "forexfactory" else "STALE/IGNORE"
            if r["status"] == "ignored":
                rel = "ignored"
            flags = []
            if r.get("sent_alert_60"):
                flags.append("1h")
            if r.get("sent_alert_15"):
                flags.append("15m")
            if r.get("sent_post_release"):
                flags.append("post")
            flag_s = ",".join(flags) if flags else "no alerts"
            print(
                f"    [{r['source']}|{r['status']}] {r['scheduled_at_sgt']}  {r['event_name']}"
                f"  ({rel}; sent: {flag_s})"
            )
    else:
        print("    (no rows)")


def main() -> None:
    args = _parse_args()
    from_date, to_date = _date_range(args)

    print("WGC XAUUSD — News audit")
    print(f"Range: {from_date} .. {to_date} (SGT days)")
    print("Source: Forex Factory USD red/orange + src/news_filter.py (XAUUSD rules)")

    try:
        raw = forex_factory.load_raw_rows(refresh=args.refresh)
    except forex_factory.ForexFactoryError as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)

    ff_rows = _ff_usd_rows(raw, from_date, to_date)
    if not ff_rows:
        print("\nWARNING: No FF USD red/orange rows in range.")
        print("FF weekly export only covers ~current week; older dates may be empty.")

    db_by_day = {}
    if not args.no_db:
        try:
            for row in _db_rows(from_date, to_date):
                key = row["scheduled_at_sgt"].strftime("%Y-%m-%d") if hasattr(row["scheduled_at_sgt"], "strftime") else str(row["scheduled_at_sgt"])[:10]
                db_by_day.setdefault(key, []).append(row)
        except Exception as exc:
            print(f"\nWARNING: DB read failed: {exc}")

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    day = start
    while day <= end:
        _print_day(day.isoformat(), ff_rows, db_by_day)
        day += timedelta(days=1)

    tracked_n = sum(
        1 for item in ff_rows
        if news_filter.drop_reason((item["raw"].get("title") or "")) is None
    )
    print(f"\n{'=' * 72}")
    print(f"Summary: {tracked_n} tracked / {len(ff_rows)} FF USD red-orange in range")
    print("Legend: RED=high impact, ORG=medium impact")
    print(f"{'=' * 72}\n")


if __name__ == "__main__":
    main()
