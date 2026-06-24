"""Chart view helpers — two Asian session framing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from charts import _layout_zoom_for_hours  # noqa: E402


class LayoutZoomTests(unittest.TestCase):
    def test_two_session_span_tight_with_heavy_move_right(self):
        zoom, left, right, zin = _layout_zoom_for_hours(45.0, 800)
        self.assertGreaterEqual(zoom, 3)
        self.assertGreaterEqual(left, 8)
        self.assertGreaterEqual(right, 12)
        self.assertGreaterEqual(zin, 2)

    def test_short_span_still_pans(self):
        zoom, left, right, zin = _layout_zoom_for_hours(8.0, 800)
        self.assertGreaterEqual(right, 10)
        self.assertGreaterEqual(zin, 1)


if __name__ == "__main__":
    unittest.main()
