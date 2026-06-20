#!/usr/bin/env python
"""12:00 PM SGT daily — USD News Outlook (Task 1)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

import logging  # noqa: E402
from datetime import datetime  # noqa: E402

from config import config  # noqa: E402
import calendar_service  # noqa: E402
import db  # noqa: E402
import templates  # noqa: E402
import telegram_client  # noqa: E402

log = logging.getLogger("job.outlook")


def main() -> None:
    now_sgt = datetime.now(config.tz)
    try:
        events = calendar_service.get_outlook(now_sgt)
    except Exception as exc:  # calendar down -> admin alert, no member message
        log.exception("Calendar fetch failed")
        db.audit("outlook", "error", error_message=str(exc))
        try:
            telegram_client.send_message(templates.admin_alert_calendar(),
                                         chat_id=config.wgc_bots_chat_id)
        except Exception:
            log.exception("Admin alert send failed")
        return

    for e in events:
        try:
            db.upsert_event(e)
        except Exception:
            log.warning("Upsert failed for %s", e.event_name)

    tdicts = [e.template_dict() for e in events]
    message = templates.daily_outlook(now_sgt.strftime("%d %b %Y"), tdicts)
    telegram_client.send_message(message)
    db.audit("outlook", "sent", output_json=f"{len(events)} events")
    log.info("Outlook published (%d events)", len(events))


if __name__ == "__main__":
    main()
