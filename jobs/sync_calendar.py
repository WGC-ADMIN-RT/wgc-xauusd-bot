#!/usr/bin/env python
"""Sync Forex Factory tracked events into economic_events (for alerts)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import calendar_service  # noqa: E402

if __name__ == "__main__":
    n = calendar_service.sync_to_db()
    print(f"Synced {n} tracked event(s) to the database.")
