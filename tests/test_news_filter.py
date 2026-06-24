"""Tests for XAUUSD relevance filtering of FF calendar events."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import news_filter  # noqa: E402


def _ev(name: str, utc_key: str = "2026-06-25T12:30:00+00:00"):
  return SimpleNamespace(event_name=name, scheduled_utc=utc_key)


class XauusdRelevanceTests(unittest.TestCase):
    def test_keeps_core_macro(self):
        for name in (
            "Core PCE Price Index m/m",
            "Non-Farm Employment Change",
            "Unemployment Claims",
            "ISM Manufacturing PMI",
            "JOLTS Job Openings",
            "CB Consumer Confidence",
            "Final GDP q/q",
            "ADP Non-Farm Employment Change",
            "CPI m/m",
            "Fed Chair Powell Speaks",
            "FOMC Press Conference",
            "President Trump Speaks",
        ):
            with self.subTest(name=name):
                self.assertTrue(news_filter.is_xauusd_relevant(name), name)

    def test_drops_non_gold_events(self):
        for name in (
            "President Biden Speaks",
            "Fed Bank Stress Test Results",
            "Current Account",
            "MBA 30-Year Mortgage Rate",
            "EIA Crude Oil Inventories",
            "10-Year Note Auction",
            "Fed Balance Sheet",
            "Beige Book",
            "FOMC Member Williams Speaks",
        ):
            with self.subTest(name=name):
                self.assertFalse(news_filter.is_xauusd_relevant(name), name)

    def test_apply_keeps_trump_and_pce(self):
        events = [
            _ev("President Trump Speaks", "2026-06-24T02:05:00+00:00"),
            _ev("Core PCE Price Index m/m"),
        ]
        kept = news_filter.apply(events)
        names = [e.event_name for e in kept]
        self.assertEqual(
            names,
            ["President Trump Speaks", "Core PCE Price Index m/m"],
        )

    def test_composite_pmi_dedup(self):
        t = "2026-06-23T13:45:00+00:00"
        events = [
            _ev("Flash Manufacturing PMI", t),
            _ev("Flash Services PMI", t),
            _ev("Composite PMI", t),
        ]
        kept = news_filter.apply(events)
        names = [e.event_name for e in kept]
        self.assertNotIn("Composite PMI", names)
        self.assertEqual(len(names), 2)


if __name__ == "__main__":
    unittest.main()
