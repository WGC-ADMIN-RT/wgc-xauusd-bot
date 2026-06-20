"""SGT self-gating for daily jobs — makes scheduling DST-proof.

The server runs on US Eastern (EDT/EST), so a fixed cron time drifts vs SGT across
DST changes. Instead, the daily jobs run on a frequent cron (every 5 min) and gate
themselves: they only fire when the current SGT time is inside the target window,
and a once-per-day marker guarantees a single run.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from config import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MARK_DIR = os.path.join(_ROOT, "logs")


def _marker(name: str) -> str:
    today = datetime.now(config.tz).strftime("%Y-%m-%d")
    return os.path.join(_MARK_DIR, f".{name}_{today}")


def daily_due(target_hhmm: str, window_minutes: int, name: str) -> bool:
    """True if SGT now is within [target, target+window) and not already done today."""
    now = datetime.now(config.tz)
    hh, mm = map(int, target_hhmm.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    in_window = target <= now < target + timedelta(minutes=window_minutes)
    return in_window and not os.path.exists(_marker(name))


def mark_done(name: str) -> None:
    os.makedirs(_MARK_DIR, exist_ok=True)
    open(_marker(name), "w").close()
    _cleanup_old_markers(name)


def _cleanup_old_markers(name: str) -> None:
    """Drop markers older than 7 days so logs/ doesn't accumulate empties."""
    cutoff = datetime.now(config.tz) - timedelta(days=7)
    try:
        for f in os.listdir(_MARK_DIR):
            if f.startswith(f".{name}_"):
                try:
                    d = datetime.strptime(f.split("_", 1)[1], "%Y-%m-%d")
                    if config.tz.localize(d) < cutoff:
                        os.remove(os.path.join(_MARK_DIR, f))
                except (ValueError, IndexError):
                    pass
    except FileNotFoundError:
        pass
