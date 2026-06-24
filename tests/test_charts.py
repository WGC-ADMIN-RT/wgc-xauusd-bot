"""Chart view helpers — two Asian session framing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from charts import _layout_zoom_for_hours  # noqa: E402


class LayoutZoomTests(unittest.TestCase):
    def test_two_session_preset(self):
        zoom, left, right = _layout_zoom_for_hours(33.5, 800)
        self.assertGreaterEqual(zoom, 4)
        self.assertLessEqual(zoom, 6)
        self.assertGreaterEqual(left, 11)
        self.assertGreaterEqual(right, 16)

    def test_overnight_before_asian(self):
        zoom, left, right = _layout_zoom_for_hours(45.0, 800)
        self.assertGreaterEqual(left, 11)
        self.assertLessEqual(zoom, 6)


if __name__ == "__main__":
    unittest.main()
