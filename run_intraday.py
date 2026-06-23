#!/usr/bin/env python
"""Cron launcher — some crons call run_intraday.py at repo root; job lives in jobs/."""
import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs", "run_intraday.py"),
    run_name="__main__",
)
