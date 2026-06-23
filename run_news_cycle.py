#!/usr/bin/env python
"""Cron launcher — some crons call run_news_cycle.py at repo root; job lives in jobs/."""
import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs", "run_news_cycle.py"),
    run_name="__main__",
)
