"""XAUUSD relevance filter for Forex Factory USD red/orange events.

FF shows every USD high/medium folder; gold traders only care about a subset that
moves the dollar, rates, or risk — the drivers of XAUUSD. This module is the gate:

* **Allowlist** — macro releases and Fed events that routinely affect gold.
* **Denylist** — energy inventories, auctions, mortgage prints, stress tests, etc.
* **Speech rule** — generic political/Fed-member speeches are dropped; Fed Chair /
  FOMC press events and **President Trump** speeches are kept.
* **PMI de-dup** — drop Composite PMI when Flash Mfg + Flash Services share a slot.
* **Display names** — FF-style PMI labels (render only).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import List

log = logging.getLogger("news_filter")

# Macro releases / Fed events that matter for XAUUSD (first match wins in apply).
_INCLUDE = re.compile(
    r"\bcpi\b|consumer price|inflation rate|core inflation"
    r"|\bppi\b|producer price"
    r"|core pce|\bpce\b|pce price"
    r"|non.?farm|\bnfp\b|payrolls?|employment change|\badp\b"
    r"|unemployment rate|unemployment claims|jobless claims|initial claims|continuing claims|\bjolts\b"
    r"|retail sales|\bgdp\b|trade balance"
    r"|\bpmi\b|\bism\b|manufacturing index|services index"
    r"|philly fed|empire state|chicago pmi|richmond fed|dallas fed|kansas city fed"
    r"|durable goods|factory orders|industrial production|capacity utilization"
    r"|consumer confidence|consumer sentiment|umich|u\.?o\.?m|michigan sentiment"
    r"|housing starts|building permits|new home sales|existing home sales|pending home sales"
    r"|fomc|fed funds|federal funds|interest rate decision|monetary policy statement"
    r"|fomc minutes|fed chair|powell|fomc press conference|fomc statement",
    re.IGNORECASE,
)

# Always drop — not actionable for gold even if FF marks them orange/red.
_EXCLUDE = re.compile(
    r"crude oil|oil stock|natural gas|gasoline|distillate|cushing|heating oil"
    r"|rig count|baker hughes"
    r"|\bauction\b|treasury (bill|note|bond)"
    r"|fed balance sheet|reserve balances|money supply|\bm2\b|reverse repo"
    r"|redbook|ibd/tipp|tipp economic"
    r"|\bmba\b|mortgage rate|mortgage applications|mortgage delinquenc"
    r"|wasde|crop production|grain stocks"
    r"|stress test|bank stress"
    r"|beige book|loan officer survey|senior loan officer"
    r"|wholesale inventories|business inventories|retail inventories"
    r"|consumer credit|vehicle sales|chain store"
    r"|current account",
    re.IGNORECASE,
)

# Speech-shaped rows: keep Fed Chair / FOMC press / President Trump (risk & USD moves).
_SPEECH = re.compile(r"\b(speaks?|speech|testif|press conference)\b", re.IGNORECASE)
_FED_SPEECH_OK = re.compile(
    r"fed chair|fomc press|powell|fomc statement|monetary policy",
    re.IGNORECASE,
)
_TRUMP_SPEECH_OK = re.compile(r"president trump|\btrump\b", re.IGNORECASE)

_COMPOSITE_PMI = re.compile(r"composite\s+pmi", re.IGNORECASE)
_MFG_PMI = re.compile(r"manufacturing\s+pmi", re.IGNORECASE)
_SVC_PMI = re.compile(r"services\s+pmi", re.IGNORECASE)
_FLASH_HINT = re.compile(r"flash|s&p|markit", re.IGNORECASE)


def is_xauusd_relevant(name: str) -> bool:
    """True when an FF USD red/orange event should be tracked for XAUUSD."""
    n = (name or "").strip()
    if not n or _EXCLUDE.search(n):
        return False
    if _TRUMP_SPEECH_OK.search(n):
        return True
    if not _INCLUDE.search(n):
        return False
    if _SPEECH.search(n) and not _FED_SPEECH_OK.search(n):
        return False
    return True


def _drop_composite_pmi(events: List) -> List:
    """Remove Composite PMI when Flash Mfg + Flash Services share the same release time."""
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
    """Keep only XAUUSD-relevant FF events (order preserved)."""
    before = len(events)
    kept = [e for e in events if is_xauusd_relevant(e.event_name)]
    after_relevance = len(kept)
    kept = _drop_composite_pmi(kept)

    dropped = before - after_relevance
    composite_n = after_relevance - len(kept)
    if dropped or composite_n:
        log.info(
            "XAUUSD filter: dropped %d non-relevant + %d composite-PMI (%d -> %d)",
            dropped, composite_n, before, len(kept),
        )
    return kept


def display_name(name: str) -> str:
    """FF-style display label. Render-only — never use for calendar matching/storage."""
    n = (name or "").strip()
    low = n.lower()
    if "composite pmi" in low:
        return n
    if "manufacturing pmi" in low and _FLASH_HINT.search(low):
        return "Flash Manufacturing PMI"
    if "services pmi" in low and _FLASH_HINT.search(low):
        return "Flash Services PMI"
    return n
