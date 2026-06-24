#!/usr/bin/env python
"""Runs every minute — pre-news alerts (1h, 15m) and post-release breakdown (Task 1).

Idempotent: each event carries sent_alert_60 / sent_alert_15 / sent_post_release flags,
so a given alert fires exactly once even though this job runs every minute.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

import logging  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytz  # noqa: E402

from config import config  # noqa: E402
import calendar_service  # noqa: E402
import news_filter  # noqa: E402
import market_data  # noqa: E402
import polarity  # noqa: E402
import db  # noqa: E402
import templates  # noqa: E402
import telegram_client  # noqa: E402

log = logging.getLogger("job.news")

# Bounded fire windows (minutes before release) — robust to cron jitter, avoids
# firing a "1 hour before" alert when the bot only just started near the event.
ALERT_60_FROM, ALERT_60_TO = 45, 60
ALERT_15_FROM, ALERT_15_TO = 10, 15
POSTRELEASE_MAX_AGE = 20
ACTUAL_GIVEUP_MINUTES = 15
POSTRELEASE_MIN_AGE = 5  # wait for T+5 M5 close before publishing price reaction


def _time_sgt(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _row_event(row: dict) -> dict:
    return {
        "time_sgt": _time_sgt(row["scheduled_at_sgt"]),
        "impact": row["impact"],
        "event_name": news_filter.display_name(row["event_name"]),
        "forecast": row["forecast"],
        "previous": row["previous"],
        "actual": row.get("actual"),
    }


def _group_same_time(rows):
    groups, current, key = [], [], None
    for r in rows:
        if r["scheduled_at_utc"] != key:
            if current:
                groups.append(current)
            current, key = [r], r["scheduled_at_utc"]
        else:
            current.append(r)
    if current:
        groups.append(current)
    return groups


def _send_grouped(group, builder, field):
    """Send one alert per same-time group (spec: 'one alert per event group')."""
    primary = max(group, key=lambda r: 1 if r["impact"] == "high" else 0)
    message = builder(_row_event(primary))
    if len(group) > 1:
        others = ", ".join(news_filter.display_name(r["event_name"]) for r in group if r["id"] != primary["id"])
        message += f"\n\nAlso releasing at this time: {others}"
    telegram_client.send_message(message)
    for r in group:
        db.mark_sent(r["id"], field)
    log.info("%s alert sent for %s (+%d grouped)", field, primary["event_name"], len(group) - 1)


def _guard_relevant(rows):
    """Drop stale/irrelevant DB rows; suppress so they never alert again."""
    kept = []
    for r in rows:
        legacy_fmp = (r.get("source") or "").lower() == "fmp"
        if legacy_fmp or not news_filter.is_xauusd_relevant(r["event_name"]):
            db.suppress_event(r["id"])
            log.info("Suppressed non-relevant event: %s (source=%s)",
                     r["event_name"], r.get("source"))
        else:
            kept.append(r)
    return kept


def _do_alerts(now_utc):
    for field, lo, hi, builder in (
        ("sent_alert_60", ALERT_60_FROM, ALERT_60_TO, templates.alert_1h),
        ("sent_alert_15", ALERT_15_FROM, ALERT_15_TO, templates.warning_15m),
    ):
        rows = db.fetch_for_alert(field, now_utc + timedelta(minutes=lo),
                                  now_utc + timedelta(minutes=hi))
        rows = _guard_relevant(rows)
        for group in _group_same_time(rows):
            try:
                _send_grouped(group, builder, field)
            except Exception:
                log.exception("Alert send failed (%s)", field)


def _do_post_release(now_utc):
    rows = db.fetch_for_postrelease(now_utc, POSTRELEASE_MAX_AGE)
    for row in _guard_relevant(rows):
        sched = row["scheduled_at_utc"]
        if sched.tzinfo is None:
            sched = pytz.UTC.localize(sched)
        age_min = (now_utc - sched).total_seconds() / 60.0
        try:
            actual = calendar_service.fetch_actual(row["event_name"], sched)
            if actual and age_min >= POSTRELEASE_MIN_AGE:
                _publish_release(row, sched, actual, now_utc)
            elif age_min >= ACTUAL_GIVEUP_MINUTES and not actual:
                telegram_client.send_message(templates.actual_unavailable())
                db.mark_sent(row["id"], "sent_post_release")
                log.info("Actual unavailable after %dm for %s", ACTUAL_GIVEUP_MINUTES, row["event_name"])
            # else: keep polling next minute
        except Exception:
            log.exception("Post-release handling failed for %s", row["event_name"])


def _publish_release(row, sched_utc, actual, now_utc):
    pol = polarity.evaluate(row["event_name"], actual, row["forecast"], row["previous"])
    reaction = market_data.get_price_reaction(sched_utc, now_utc=now_utc)
    ref_price = (
        reaction.get("price_plus_15")
        or reaction.get("price_plus_5")
        or reaction.get("current_price")
    )
    confirmation = market_data.price_action_confirmation(
        pol.xau_bias, reaction["price_before"], ref_price
    )
    impact_summary = f"Initial read: XAUUSD {pol.label}."
    if pol.label == "MIXED":
        impact_summary += " Conflicting vs forecast/previous — wait for price confirmation."
    elif pol.label == "NEUTRAL":
        impact_summary += " In line — limited directional bias."
    event = {"event_name": news_filter.display_name(row["event_name"]), "actual": actual,
             "forecast": row["forecast"], "previous": row["previous"]}
    reaction_block = {
        "usd_bias_summary": pol.reason,
        "price_before": reaction["price_before"],
        "price_at_release": reaction["price_at_release"],
        "price_plus_5": reaction["price_plus_5"],
        "price_plus_15": reaction["price_plus_15"],
        "current_price": reaction["current_price"],
        "price_change": reaction["price_change"],
        "price_confirmation": confirmation,
        "xauusd_impact_summary": impact_summary,
        "signal_status": "Signal automation remains paused until the next M5/M15 candle confirms direction.",
    }
    telegram_client.send_message(templates.post_release(event, reaction_block))
    db.update_actual(row["id"], actual, pol.xau_bias)
    db.mark_sent(row["id"], "sent_post_release")
    log.info("Post-release published for %s (actual=%s -> XAUUSD %s)",
             row["event_name"], actual, pol.label)


def main() -> None:
    now_utc = datetime.now(pytz.UTC)
    _do_alerts(now_utc)
    _do_post_release(now_utc)


if __name__ == "__main__":
    main()
