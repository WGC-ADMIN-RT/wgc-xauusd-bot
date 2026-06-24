"""Chart view helpers — two Asian session framing."""
import unittest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from charts import _layout_zoom_for_hours  # noqa: E402


class LayoutZoomTests(unittest.TestCase):
    def test_two_session_span_uses_heavy_pan(self):
        zoom, pan = _layout_zoom_for_hours(33.5, 800)
        self.assertGreaterEqual(zoom, 4)
        self.assertGreaterEqual(pan, 10)

    def test_short_span_still_pans(self):
        zoom, pan = _layout_zoom_for_hours(8.0, 800)
        self.assertGreaterEqual(zoom, 1)
        self.assertGreaterEqual(pan, 2)


if __name__ == "__main__":
    unittest.main()
