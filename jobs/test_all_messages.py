#!/usr/bin/env python
"""Phase 1 smoke test — send every Telegram message type to the publish target.

Run on the OrangeHost server (Telegram API is unreliable from Windows):

    cd ~/wgc-xauusd-bot
    git pull origin master
    .venv/bin/python jobs/test_all_messages.py

Optional:
    --skip-live   Template samples only (no live outlook/intraday/chart).
    --admin-only  Also send admin alert templates (tagged TEST).
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

import logging  # noqa: E402
from datetime import datetime  # noqa: E402

from config import config  # noqa: E402
import calendar_service  # noqa: E402
import intraday  # noqa: E402
import market_data  # noqa: E402
import polarity  # noqa: E402
import templates  # noqa: E402
import telegram_client  # noqa: E402

log = logging.getLogger("test.all")
SLEEP = 1.2  # gentle spacing for Telegram rate limits


def _send(label: str, text: str) -> None:
    header = f"🧪 TEST {label}\n{'—' * 28}\n\n"
    ids = telegram_client.send_message(header + text)
    print(f"OK  {label} (message_id={ids[0]})")
    time.sleep(SLEEP)


def _sample_event(impact: str, name: str) -> dict:
    return {
        "time_sgt": "9:30 PM",
        "impact": impact,
        "event_name": name,
        "forecast": "200K",
        "previous": "180K",
    }


def _post_release_sample(label: str, event_name: str, actual, forecast, previous) -> None:
    pol = polarity.evaluate(event_name, actual, forecast, previous)
    reaction = {
        "usd_bias_summary": pol.reason,
        "price_before": 2650.50,
        "price_at_release": 2651.20,
        "price_plus_5": 2652.80,
        "price_plus_15": 2651.90,
        "current_price": 2652.10,
        "price_change": "+1.60 (+0.06%)",
        "price_confirmation": market_data.price_action_confirmation(
            pol.xau_bias, 2650.50, 2651.90
        ),
        "xauusd_impact_summary": f"Initial read: XAUUSD {pol.label}.",
        "signal_status": "Signal automation remains paused until the next M5/M15 candle confirms direction.",
    }
    if pol.label == "MIXED":
        reaction["xauusd_impact_summary"] += " Conflicting vs forecast/previous — wait for price confirmation."
    elif pol.label == "NEUTRAL":
        reaction["xauusd_impact_summary"] += " In line — limited directional bias."
    event = {
        "event_name": event_name,
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
    }
    _send(label, templates.post_release(event, reaction))


def test_templates(include_admin: bool) -> None:
    date_sgt = datetime.now(config.tz).strftime("%A, %d %b %Y")

    _send("0/12 — wiring", (
        "WGC XAUUSD Bot — full message smoke test\n"
        f"Time: {datetime.now(config.tz).strftime('%d %b %Y %I:%M %p')} SGT\n"
        f"Publish target: {config.publish_target}\n"
        f"Chat: {config.target_chat_id}"
    ))

    _send("1/12 — outlook (no events)", templates.daily_outlook_no_events(date_sgt))

    ev = _sample_event("high", "Non-Farm Payrolls")
    ev_med = _sample_event("medium", "Flash Manufacturing PMI")
    grouped = [
        {"time_sgt": "9:45 PM", "impact": "medium", "event_name": "Flash Manufacturing PMI",
         "forecast": "54.8", "previous": "55.1"},
        {"time_sgt": "9:45 PM", "impact": "medium", "event_name": "Flash Services PMI",
         "forecast": "51.0", "previous": "50.7"},
    ]
    _send("2/12 — outlook (sample events)", templates.daily_outlook(date_sgt, grouped))

    _send("3/12 — alert 1h (high)", templates.alert_1h(ev))
    _send("4/12 — alert 1h (medium)", templates.alert_1h(ev_med))
    _send("5/12 — warning 15m (high)", templates.warning_15m(ev))
    _send("6/12 — warning 15m (medium)", templates.warning_15m(ev_med))

    _post_release_sample("7/12 — post-release (bearish)", "Non-Farm Payrolls", "250K", "200K", "180K")
    _post_release_sample("8/12 — post-release (MIXED)", "Non-Farm Payrolls", "175K", "200K", "150K")
    _post_release_sample("9/12 — post-release (NEUTRAL)", "Non-Farm Payrolls", "200K", "200K", "180K")

    _send("10/12 — actual unavailable", templates.actual_unavailable())

    if include_admin:
        _send("11a — admin calendar [TEST]", templates.admin_alert_calendar())
        _send("11b — admin market data [TEST]", templates.admin_alert_market_data())


def test_live_outlook() -> None:
    now_sgt = datetime.now(config.tz)
    events = calendar_service.get_outlook(now_sgt)
    tdicts = [e.template_dict() for e in events]
    msg = templates.daily_outlook(now_sgt.strftime("%A, %d %b %Y"), tdicts)
    _send(f"LIVE — outlook ({len(events)} events)", msg)


def test_live_intraday() -> None:
    now_sgt = datetime.now(config.tz)
    try:
        events = [e for e in calendar_service.get_outlook(now_sgt) if e.scheduled_sgt > now_sgt]
    except Exception:
        events = []
    upcoming = [{
        "impact": e.impact, "event_name": e.event_name,
        "time_sgt": e.template_dict()["time_sgt"], "is_major": e.is_major,
    } for e in events]

    snapshot = market_data.build_snapshot(upcoming_usd_news=upcoming)
    plan = intraday.build_plan(snapshot, upcoming)

    import charts  # noqa: WPS433 — optional dependency for photo test
    chart_path = charts.render(snapshot)

    if chart_path:
        telegram_client.send_photo(
            chart_path,
            caption=f"🧪 TEST LIVE — intraday + chart\n{'—' * 28}\n\n{plan['member_message']}",
        )
        print(f"OK  LIVE — intraday + chart ({chart_path})")
    else:
        _send("LIVE — intraday (no chart)", plan["member_message"] + "\n\n(Chart image unavailable this session.)")
    time.sleep(SLEEP)


def main() -> None:
    problems = config.validate()
    if problems:
        print("Config problems:", "; ".join(problems))
        sys.exit(1)

    skip_live = "--skip-live" in sys.argv
    include_admin = "--admin-only" in sys.argv or not skip_live

    print(f"Target chat: {config.target_chat_id} ({config.publish_target})")
    test_templates(include_admin=include_admin)

    if not skip_live:
        print("--- live jobs ---")
        test_live_outlook()
        test_live_intraday()

    _send("DONE", "Full smoke test complete. Check this chat for all message types above.")
    print("\nAll tests sent. Review the WGC Bots Telegram group.")


if __name__ == "__main__":
    main()
