#!/usr/bin/env python
"""Replay past days — send FF-filtered USD news to Telegram for testing.

Uses the same rules as production (Forex Factory USD red/orange + XAUUSD filter)
but does NOT use the database or live alert windows. Use this to verify what the
bot should have tracked on past days while you are testing.

Examples:
    .venv/bin/python jobs/replay_news.py --from 2026-06-22 --to 2026-06-24
    .venv/bin/python jobs/replay_news.py --from 2026-06-22 --to 2026-06-26 --alerts
    .venv/bin/python jobs/replay_news.py --days 5 --dry-run

Note: FF weekly export only covers ~the current week. Older dates may show no events.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import forex_factory  # noqa: E402
import news_filter  # noqa: E402
import polarity  # noqa: E402
import templates  # noqa: E402
import telegram_client  # noqa: E402
from config import config  # noqa: E402

SLEEP = 1.2


def _parse_args():
    p = argparse.ArgumentParser(description="Replay past FF news to Telegram (testing)")
    p.add_argument("--from", dest="from_date", metavar="DATE", help="Start YYYY-MM-DD (SGT day)")
    p.add_argument("--to", dest="to_date", metavar="DATE", help="End YYYY-MM-DD (inclusive)")
    p.add_argument("--days", type=int, default=3, help="Last N SGT days if --from omitted")
    p.add_argument("--refresh", action="store_true", help="Re-download FF weekly feeds")
    p.add_argument("--alerts", action="store_true", help="Also send 1h + 15m alert previews per event")
    p.add_argument("--dry-run", action="store_true", help="Print only; do not send Telegram")
    return p.parse_args()


def _date_range(args) -> tuple[str, str]:
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date or args.from_date, "%Y-%m-%d").date()
    else:
        end = datetime.now(config.tz).date()
        start = end - timedelta(days=max(1, args.days) - 1)
    return start.isoformat(), end.isoformat()


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


def _template_events(day_ff: List[dict]) -> List[dict]:
    events = []
    for item in day_ff:
        raw = item["raw"]
        name = (raw.get("title") or "").strip()
        if news_filter.drop_reason(name) is not None:
            continue
        impact = (raw.get("impact") or "").lower()
        events.append({
            "time_sgt": item["sgt"].strftime("%I:%M %p").lstrip("0"),
            "impact": impact,
            "event_name": news_filter.display_name(name),
            "forecast": raw.get("forecast") or None,
            "previous": raw.get("previous") or None,
            "short_reason": polarity.pre_release_note(name),
        })
    return events


def _dropped(day_ff: List[dict]) -> List[str]:
    lines = []
    for item in day_ff:
        raw = item["raw"]
        name = (raw.get("title") or "").strip()
        reason = news_filter.drop_reason(name)
        if reason is None:
            continue
        t = item["sgt"].strftime("%I:%M %p").lstrip("0")
        lines.append(f"  - {t}  {name}  ({reason})")
    return lines


def _send(label: str, text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"\n{'=' * 60}\n{label}\n{'=' * 60}\n{text}\n")
        return
    header = f"🧪 TEST REPLAY\n{'—' * 28}\n\n"
    ids = telegram_client.send_message(header + text)
    print(f"Sent {label} (message_id={ids[0]})")
    time.sleep(SLEEP)


def main() -> None:
    args = _parse_args()
    from_date, to_date = _date_range(args)

    try:
        raw = forex_factory.load_raw_rows(refresh=args.refresh)
    except forex_factory.ForexFactoryError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    ff_rows = _ff_usd_rows(raw, from_date, to_date)
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()

    intro = (
        f"Replay range: {from_date} .. {to_date} (SGT calendar days)\n"
        f"Rules: Forex Factory USD red/orange + XAUUSD filter\n"
        f"Target: {config.publish_target} ({config.target_chat_id})"
    )
    _send("intro", intro, args.dry_run)

    day = start
    total_tracked = 0
    while day <= end:
        day_iso = day.isoformat()
        day_ff = [r for r in ff_rows if r["sgt"].date() == day]
        tracked = _template_events(day_ff)
        total_tracked += len(tracked)
        date_label = day.strftime("%A, %d %b %Y")

        if tracked:
            body = templates.daily_outlook(date_label, tracked)
        else:
            body = templates.daily_outlook_no_events(date_label)

        dropped = _dropped(day_ff)
        if dropped:
            body += "\n\nFF showed but bot drops:\n" + "\n".join(dropped)

        body += (
            f"\n\n(Replay test for {day_iso} — not a live scheduled send.)"
        )
        _send(f"outlook — {day_iso}", body, args.dry_run)

        if args.alerts and tracked:
            for ev in tracked:
                _send(f"1h preview — {ev['event_name']}", templates.alert_1h(ev), args.dry_run)
                _send(f"15m preview — {ev['event_name']}", templates.warning_15m(ev), args.dry_run)

        day += timedelta(days=1)

    summary = f"Replay complete: {total_tracked} tracked event(s) across the range."
    _send("summary", summary, args.dry_run)
    print(summary)


if __name__ == "__main__":
    main()
