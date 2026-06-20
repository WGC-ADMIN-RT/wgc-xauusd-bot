"""Deterministic USD news -> XAUUSD bias engine.

Implements the spec's "News polarity rules" table with pure code (no AI). Given an
event name and its actual/forecast values, it returns the USD bias and the resulting
XAUUSD bias plus a human-readable interpretation.

Rule summary (higher actual than forecast):
  inflation (CPI/PPI/PCE) -> USD bullish  -> XAUUSD bearish
  NFP / payrolls          -> USD bullish  -> XAUUSD bearish
  retail/GDP/PMI/ISM      -> USD bullish  -> XAUUSD bearish
  unemployment rate       -> USD bearish  -> XAUUSD bullish   (inverse)
  jobless claims          -> USD bearish  -> XAUUSD bullish   (inverse)
  fed hawkish/higher rate -> USD bullish  -> XAUUSD bearish
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Category -> True if a HIGHER actual is USD-bullish (XAUUSD-bearish),
#             False if a HIGHER actual is USD-bearish (XAUUSD-bullish, "inverse").
_NORMAL = True
_INVERSE = False

# Ordered keyword matching: first match wins. Keep specific terms before generic ones.
_CATEGORY_RULES = [
    ("unemployment_rate", _INVERSE, [r"\bunemployment rate\b"]),
    ("jobless_claims", _INVERSE, [r"jobless claims", r"initial claims", r"continuing claims"]),
    ("inflation", _NORMAL, [r"\bcpi\b", r"\bppi\b", r"\bpce\b", r"consumer price",
                            r"producer price", r"inflation", r"core price"]),
    ("nfp", _NORMAL, [r"non.?farm", r"\bnfp\b", r"payrolls?", r"employment change",
                      r"adp\b"]),
    ("growth", _NORMAL, [r"retail sales", r"\bgdp\b", r"\bpmi\b", r"\bism\b",
                         r"durable goods", r"industrial production", r"manufacturing",
                         r"services", r"consumer confidence", r"sentiment"]),
    ("fed", _NORMAL, [r"fed funds", r"rate decision", r"interest rate", r"fomc",
                      r"federal funds", r"powell", r"fed chair", r"monetary policy"]),
]

_MAJOR_EVENTS = re.compile(
    r"(\bcpi\b|consumer price|inflation rate|core inflation"  # CPI variants
    r"|core pce|\bpce\b|pce price"                            # PCE variants
    r"|non.?farm|\bnfp\b|payrolls?"                           # NFP variants
    r"|fomc|rate decision|fed funds|federal funds|interest rate decision)",  # Fed
    re.IGNORECASE,
)


@dataclass
class PolarityResult:
    category: str               # e.g. "inflation", "nfp", "unknown"
    is_major: bool              # CPI/NFP/FOMC/PCE -> wider blackout, stricter handling
    usd_bias: str               # "bullish" | "bearish" | "neutral"
    xau_bias: str               # "bullish" | "bearish" | "neutral"
    label: str                  # "BULLISH" | "BEARISH" | "NEUTRAL" (XAUUSD)
    reason: str                 # short human explanation
    surprise: Optional[float]   # actual - forecast (None if not numeric)


def classify_event(event_name: str) -> tuple[str, bool]:
    """Return (category, normal_direction). normal_direction True == higher is XAU-bearish."""
    name = (event_name or "").lower()
    for category, direction, patterns in _CATEGORY_RULES:
        if any(re.search(p, name) for p in patterns):
            return category, direction
    return "unknown", _NORMAL


def is_major_event(event_name: str) -> bool:
    return bool(_MAJOR_EVENTS.search(event_name or ""))


def _to_float(value) -> Optional[float]:
    """Parse FMP/FF style values like '3.2%', '236K', '1.25M', '-0.1'."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if s == "" or s.lower() in {"n/a", "na", "-", "tentative"}:
        return None
    mult = 1.0
    if s and s[-1] in "KkMmBb":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[s[-1].lower()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def evaluate(event_name: str, actual, forecast, previous=None) -> PolarityResult:
    """Compute USD/XAUUSD bias from actual vs forecast (falling back to previous)."""
    category, normal_direction = classify_event(event_name)
    major = is_major_event(event_name)

    a = _to_float(actual)
    f = _to_float(forecast)
    p = _to_float(previous)
    baseline = f if f is not None else p

    if a is None or baseline is None:
        return PolarityResult(
            category=category, is_major=major,
            usd_bias="neutral", xau_bias="neutral", label="NEUTRAL",
            reason="Actual or forecast not numeric/available yet — waiting for price confirmation.",
            surprise=None,
        )

    surprise = a - baseline
    # Treat a tiny deviation (<= 0.5% of baseline magnitude, or near-zero) as in-line.
    tolerance = max(abs(baseline) * 0.005, 1e-9)
    if abs(surprise) <= tolerance:
        return PolarityResult(
            category=category, is_major=major,
            usd_bias="neutral", xau_bias="neutral", label="NEUTRAL",
            reason="Actual came in line with forecast — limited directional bias.",
            surprise=surprise,
        )

    higher = surprise > 0
    # USD bullish when (higher and normal) or (lower and inverse)
    usd_bullish = (higher and normal_direction) or ((not higher) and (not normal_direction))
    usd_bias = "bullish" if usd_bullish else "bearish"
    # XAUUSD is inverse to USD
    xau_bias = "bearish" if usd_bullish else "bullish"
    label = xau_bias.upper()

    direction_word = "higher" if higher else "lower"
    against = "forecast" if f is not None else "previous"
    reason = (
        f"Actual {direction_word} than {against} -> USD {usd_bias}, "
        f"so XAUUSD {xau_bias}."
    )
    return PolarityResult(
        category=category, is_major=major,
        usd_bias=usd_bias, xau_bias=xau_bias, label=label,
        reason=reason, surprise=surprise,
    )


def pre_release_note(event_name: str) -> str:
    """One-line 'why it matters for XAUUSD' shown before the actual is out."""
    category, _ = classify_event(event_name)
    notes = {
        "inflation": "Hotter inflation supports USD and usually pressures gold lower.",
        "nfp": "Strong jobs data supports USD and usually pressures gold lower.",
        "growth": "Stronger growth data supports USD and usually pressures gold lower.",
        "unemployment_rate": "Higher unemployment weakens USD and usually supports gold.",
        "jobless_claims": "Higher claims weaken USD and usually support gold.",
        "fed": "A hawkish Fed supports USD and pressures gold; dovish does the opposite.",
        "unknown": "Watch the USD reaction — stronger USD typically pressures gold.",
    }
    return notes[category]
