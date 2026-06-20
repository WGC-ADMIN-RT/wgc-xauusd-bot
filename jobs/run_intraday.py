#!/usr/bin/env python
"""2:30 PM SGT daily — XAUUSD Intraday Analysis (Task 2)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

import logging  # noqa: E402
from datetime import datetime  # noqa: E402

from config import config  # noqa: E402
import calendar_service  # noqa: E402
import market_data  # noqa: E402
import charts  # noqa: E402
import intraday  # noqa: E402
import db  # noqa: E402
import templates  # noqa: E402
import telegram_client  # noqa: E402
import schedule_guard  # noqa: E402

log = logging.getLogger("job.intraday")


def _upcoming(now_sgt):
    try:
        events = [e for e in calendar_service.get_outlook(now_sgt) if e.scheduled_sgt > now_sgt]
    except Exception:
        log.warning("Could not fetch upcoming news for intraday news-risk")
        return []
    return [{
        "impact": e.impact, "event_name": e.event_name,
        "time_sgt": e.template_dict()["time_sgt"], "is_major": e.is_major,
    } for e in events]


def main() -> None:
    # SGT self-gate (DST-proof): only fire inside the 14:30 SGT window, once/day.
    if "--force" not in sys.argv and not schedule_guard.daily_due(config.intraday_analysis_sgt, 10, "intraday"):
        return
    now_sgt = datetime.now(config.tz)
    upcoming = _upcoming(now_sgt)

    try:
        snapshot = market_data.build_snapshot(upcoming_usd_news=upcoming)
    except Exception as exc:  # market data down -> admin alert, no plan
        log.exception("Market data failed")
        db.audit("intraday", "error", error_message=str(exc))
        try:
            telegram_client.send_message(templates.admin_alert_market_data(),
                                         chat_id=config.wgc_bots_chat_id)
        except Exception:
            log.exception("Admin alert send failed")
        return

    chart_path = charts.render(snapshot)            # None on render failure
    plan = intraday.build_plan(snapshot, upcoming)

    try:
        db.insert_intraday(intraday.to_db_row(snapshot, plan, chart_path))
    except Exception:
        log.warning("intraday_analyses insert failed")

    if chart_path:
        telegram_client.send_photo(chart_path, caption=plan["member_message"])
    else:  # spec: still publish the plan, but state the chart was unavailable
        telegram_client.send_message(plan["member_message"] + "\n\n(Chart image unavailable this session.)")

    db.audit("intraday", "sent", output_json=f"bias={plan['bias']} plan={plan['preferred_plan']}")
    schedule_guard.mark_done("intraday")
    log.info("Intraday plan published (bias=%s)", plan["bias"])


if __name__ == "__main__":
    main()
