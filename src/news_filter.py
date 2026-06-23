"""Forex-Factory-style trimming of the FMP USD calendar.

FMP returns more USD rows than Forex Factory shows as red/orange folders. The
member-facing outlook must match what FF displays for XAUUSD traders, not the raw
FMP feed. This module is the single place that decides "would Forex Factory show
this?":

* **Impact** is already constrained to high+medium upstream (``config.news_impacts``)
  — that is our proxy for FF red (high) / orange (medium); low is dropped there.
* **Exclusions** (this module): energy-inventory prints, treasury auctions and other
  derived/duplicate rows that FF does not treat as major USD events for gold —
  dropped even when FMP rates them medium/high.
* **PMI de-dup**: when Flash Manufacturing PMI *and* Flash Services PMI release at the
  same time, the Composite PMI is dropped (FF shows the two flashes, not the
  composite). Composite is only kept if it stands alone.
* **Display names**: the S&P Global flash PMIs are relabelled to FF's "Flash
  Manufacturing/Services PMI" wording. This is render-only — the stored event name
  stays raw so post-release ``fetch_actual`` lookups still match FMP exactly.

If FMP and FF disagree, FF wins: this filter exists to make FMP look like FF.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import List

log = logging.getLogger("news_filter")

# Event-name patterns FF does NOT surface as major USD folders for gold trading.
# Case-insensitive; any hit -> the event is dropped before it reaches the DB, so it
# also never triggers a 1h/15m alert or post-release breakdown.
_EXCLUDE = re.compile(
    r"crude oil"                      # EIA Crude Oil Inventories / API crude
    r"|oil stock"                     # API Crude Oil Stock Change
    r"|natural gas"                   # EIA Natural Gas Storage
    r"|gasoline"                      # gasoline inventories / stocks
    r"|distillate"                    # distillate stocks
    r"|cushing"                       # Cushing crude inventories
    r"|heating oil"
    r"|rig count|baker hughes"        # oil/gas rig counts
    r"|\bauction\b"                   # 10-Year Note / 30-Year Bond / Bill auctions
    r"|treasury (bill|note|bond)"
    r"|fed balance sheet|reserve balances|money supply|\bm2\b|reverse repo"  # derived monetary
    r"|redbook|ibd/tipp|tipp economic"                    # minor/derived sentiment
    r"|mba mortgage|mortgage applications|mortgage delinquenc"  # MBA weekly mortgage prints
    r"|wasde|crop production|grain stocks",               # agricultural reports
    re.IGNORECASE,
)

# PMI helpers. "composite" must not also count as manufacturing/services.
_COMPOSITE_PMI = re.compile(r"composite\s+pmi", re.IGNORECASE)
_MFG_PMI = re.compile(r"manufacturing\s+pmi", re.IGNORECASE)
_SVC_PMI = re.compile(r"services\s+pmi", re.IGNORECASE)

# S&P Global / Markit flash PMI variants -> FF-style label (render only).
_FLASH_HINT = re.compile(r"flash|s&p|markit", re.IGNORECASE)


def _excluded(name: str) -> bool:
    return bool(_EXCLUDE.search(name or ""))


def _drop_composite_pmi(events: List) -> List:
    """Remove Composite PMI only when its Flash Mfg + Flash Services siblings share
    the exact release time (FF style). A standalone composite is left untouched."""
    by_time = defaultdict(list)
    for e in events:
        by_time[e.scheduled_utc].append(e)

    drop = set()
    for group in by_time.values():
        has_mfg = any(_MFG_PMI.search(e.event_name or "") for e in group)
        has_svc = any(_SVC_PMI.search(e.event_name or "") for e in group)
        if has_mfg and has_svc:
            for e in group:
                if _COMPOSITE_PMI.search(e.event_name or ""):
                    drop.add(id(e))
    return [e for e in events if id(e) not in drop]


def apply(events: List) -> List:
    """FF-style filter over normalized Events. Returns the kept subset (order kept)."""
    before = len(events)
    kept = [e for e in events if not _excluded(e.event_name)]
    after_exclude = len(kept)
    kept = _drop_composite_pmi(kept)

    excluded_n = before - after_exclude
    composite_n = after_exclude - len(kept)
    if excluded_n or composite_n:
        log.info("FF filter: dropped %d excluded + %d composite-PMI (%d -> %d)",
                 excluded_n, composite_n, before, len(kept))
    return kept


def display_name(name: str) -> str:
    """FF-style display label. Render-only — never use for FMP matching/storage."""
    n = (name or "").strip()
    low = n.lower()
    if "composite pmi" in low:
        return n
    if "manufacturing pmi" in low and _FLASH_HINT.search(low):
        return "Flash Manufacturing PMI"
    if "services pmi" in low and _FLASH_HINT.search(low):
        return "Flash Services PMI"
    return n
