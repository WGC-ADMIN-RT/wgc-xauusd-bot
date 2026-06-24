#!/usr/bin/env python
"""One-off: suppress stale FMP / non-relevant rows in economic_events."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import db  # noqa: E402

if __name__ == "__main__":
    n = db.suppress_non_relevant_scheduled()
    print(f"Suppressed {n} scheduled event(s).")
