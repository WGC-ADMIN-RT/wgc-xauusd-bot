"""Tests for Forex Factory calendar parsing."""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import forex_factory  # noqa: E402
import calendar_service  # noqa: E402


_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ff_thisweek_sample.json")


class ForexFactoryTests(unittest.TestCase):
    def setUp(self):
        with open(_FIXTURE, encoding="utf-8") as fh:
            self.sample = json.load(fh)

    def test_normalize_keeps_ff_usd_high_medium_only(self):
        kept = [forex_factory.normalize_row(r) for r in self.sample]
        kept = [e for e in kept if e is not None]
        names = {e.event_name for e in kept}
        self.assertIn("President Trump Speaks", names)  # FF row; filtered later for XAUUSD
        self.assertIn("Core PCE Price Index m/m", names)
        self.assertNotIn("CPI m/m", names)  # CAD
        self.assertNotIn("Retail Sales m/m", names)  # low impact

    def test_trump_time_converts_to_sgt(self):
        raw = next(r for r in self.sample if r["title"] == "President Trump Speaks")
        ev = forex_factory.normalize_row(raw)
        self.assertEqual(ev.scheduled_sgt.strftime("%A"), "Wednesday")
        self.assertEqual(ev.scheduled_sgt.hour, 2)
        self.assertEqual(ev.scheduled_sgt.minute, 5)

    def test_outlook_window_excludes_fmp_only_style_events(self):
        """When FF has no events in the window, outlook must be empty — not FMP extras."""
        now_sgt = pytz.timezone("Asia/Singapore").localize(datetime(2026, 6, 24, 12, 0, 0))
        with patch.object(forex_factory, "_load_raw_rows", return_value=self.sample):
            events = calendar_service.get_outlook(now_sgt)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
