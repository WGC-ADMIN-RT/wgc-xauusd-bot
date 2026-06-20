#!/usr/bin/env python
"""One-shot: send a wiring test message into the WGC Bots group."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

from datetime import datetime  # noqa: E402

from config import config  # noqa: E402
import telegram_client  # noqa: E402


def main() -> None:
    problems = config.validate()
    stamp = datetime.now(config.tz).strftime("%d %b %Y %I:%M %p")
    text = (
        "✅ WGC XAUUSD Bot — Phase 1 wiring test\n\n"
        f"Time: {stamp} SGT\n"
        f"Publish target: {config.publish_target}\n"
        f"Chart provider: {config.chart_provider}\n"
        f"Config check: {'OK' if not problems else 'MISSING -> ' + '; '.join(problems)}"
    )
    msg_ids = telegram_client.send_message(text, chat_id=config.wgc_bots_chat_id)
    print(f"Test message sent (message_id={msg_ids}) to WGC Bots chat {config.wgc_bots_chat_id}")


if __name__ == "__main__":
    main()
