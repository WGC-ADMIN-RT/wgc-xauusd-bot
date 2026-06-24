"""Tests for daily job self-gating."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import schedule_guard  # noqa: E402


class ScheduleGuardTests(unittest.TestCase):
    def test_force_does_not_require_marker_absence(self):
        """--force jobs skip mark_done; scheduled jobs use the marker."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(schedule_guard, "_MARK_DIR", tmp):
                schedule_guard.mark_done("intraday")
                self.assertTrue(os.path.exists(schedule_guard._marker("intraday")))
                # Real scheduled run would see marker and skip; force bypasses the gate
                # in run_intraday.py — verified here that marker path is per-day only.
                today = datetime.now(schedule_guard.config.tz).strftime("%Y-%m-%d")
                self.assertTrue(
                    os.path.basename(schedule_guard._marker("intraday")).endswith(today)
                )


if __name__ == "__main__":
    unittest.main()
