"""Job bootstrap — load .env and configure logging.

MUST run before importing config (config reads the environment at import time).
Usage at the top of every job script:

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    import bootstrap; bootstrap.init()
    # ...now safe to import config-dependent modules
"""
from __future__ import annotations

import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)  # don't override real env


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logs_dir = os.path.join(_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(logs_dir, "bot.log"), encoding="utf-8"),
    ]
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def init() -> None:
    _load_env(os.path.join(_ROOT, ".env"))
    _setup_logging()
