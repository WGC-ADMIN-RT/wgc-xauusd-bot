"""Chart view helpers — two Asian session framing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from charts import _layout_zoom_for_hours  # noqa: E402


class LayoutZoomTests(unittest.TestCase):
    def test_two_session_span_balanced_not_overzoomed(self):
        zoom, left, right = _layout_zoom_for_hours(33.5, 800)
        self.assertLessEqual(zoom, 6)
        self.assertGreaterEqual(zoom, 3)
        self.assertLessEqual(left, 12)
        self.assertGreaterEqual(right, 2)

    def test_short_span_still_pans(self):
        zoom, left, right = _layout_zoom_for_hours(8.0, 800)
        self.assertGreaterEqual(zoom, 1)
        self.assertGreaterEqual(left, 2)
        self.assertGreaterEqual(right, 1)


if __name__ == "__main__":
    unittest.main()
