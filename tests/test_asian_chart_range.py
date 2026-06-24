"""Asian session chart range."""
import os
import sys
import unittest
from datetime import datetime

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import bootstrap  # noqa: E402

bootstrap.init()

from market_data import compute_asian_chart_range  # noqa: E402


class AsianChartRangeTests(unittest.TestCase):
    def test_ends_at_now_when_overnight_after_last_session(self):
        """Before 08:00 SGT, range must not stop at yesterday 16:00."""
        tz = pytz.timezone("Asia/Singapore")
        now = tz.localize(datetime(2026, 6, 25, 4, 21, 0))
        sessions = [{"date_sgt": "2026-06-23"}, {"date_sgt": "2026-06-24"}]
        r = compute_asian_chart_range(sessions, now_sgt=now)
        self.assertIsNotNone(r)
        end = datetime.fromisoformat(r["to"].replace("Z", "+00:00"))
        now_utc = now.astimezone(pytz.UTC)
        self.assertLess(abs((end - now_utc).total_seconds()), 20 * 60)


if __name__ == "__main__":
    unittest.main()
