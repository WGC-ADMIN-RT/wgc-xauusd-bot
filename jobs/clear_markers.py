#!/usr/bin/env python
"""Clear daily send markers — use before live day or after test sends.

Removes logs/.outlook_* and logs/.intraday_* so cron can fire at 12:00 / 14:30 SGT.

    .venv/bin/python jobs/clear_markers.py
    .venv/bin/python jobs/clear_markers.py --today-only   # default
    .venv/bin/python jobs/clear_markers.py --all          # all marker files
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

from config import config  # noqa: E402
from datetime import datetime  # noqa: E402

LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def main() -> None:
    p = argparse.ArgumentParser(description="Clear outlook/intraday daily markers")
    p.add_argument("--all", action="store_true", help="Remove every outlook/intraday marker")
    args = p.parse_args()

    today = datetime.now(config.tz).strftime("%Y-%m-%d")
    patterns = []
    if args.all:
        patterns = [os.path.join(LOGS, ".outlook_*"), os.path.join(LOGS, ".intraday_*")]
    else:
        patterns = [
            os.path.join(LOGS, f".outlook_{today}"),
            os.path.join(LOGS, f".intraday_{today}"),
        ]

    removed = 0
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                os.remove(path)
                print(f"removed {path}")
                removed += 1
            except FileNotFoundError:
                pass

    if removed:
        print(f"\nCleared {removed} marker(s). Outlook/intraday can fire on schedule.")
    else:
        print("No markers to clear.")


if __name__ == "__main__":
    main()
