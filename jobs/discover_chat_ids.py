#!/usr/bin/env python
"""One-shot: print the chat IDs of groups the bot can currently see (run on deploy).

Make sure someone has sent a message in each group (or the bot was just added) so the
update appears in getUpdates.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402
bootstrap.init()

import telegram_client  # noqa: E402


def main() -> None:
    ids = telegram_client.discover_chat_ids()
    if not ids:
        print("No groups visible yet. Send a message in WGC Bots and the public group, then re-run.")
        return
    print("Discovered chats (title -> chat_id):")
    for title, cid in ids.items():
        print(f"  {title}: {cid}")


if __name__ == "__main__":
    main()
