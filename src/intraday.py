"""Intraday Analysis (Task 2) — rule-based daily plan, no AI.

Consumes a market_data snapshot (+ upcoming USD news) and produces the structured
plan that fills the 📈 intraday template: bias, key support/resistance with reasons,
buy/sell scenarios, invalidation, preferred plan, and news risk.

Replaces the spec's GPT step with deterministic logic for Phase 1. The output schema
mirrors the spec's "Required JSON output" so an AI pass can be slotted in later without
changing downstream code.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import config
import templates

log = logging.getLogger("intraday")

# Two prices within this fraction of each other are treated as the same level.
_LEVEL_CLUSTER_PCT = 0.0012  # ~0.12%


# ---------------------------------------------------------------------------
# Swing levels
# ---------------------------------------------------------------------------

def _swings(df, window: int = 5) -> Tuple[List[float], List[float]]:
    highs: List[float] = []
    lows: List[float] = []
    h = df["high"].tolist()
    l = df["low"].tolist()
    n = len(df)
    for i in range(window, n - window):
        seg_h = h[i - window:i + window + 1]
        seg_l = l[i - window:i + window + 1]
        if h[i] == max(seg_h):
            highs.append(round(h[i], 2))
        if l[i] == min(seg_l):
            lows.append(round(l[i], 2))
    return highs, lows


def _dedupe_levels(levels: List[Tuple[float, str]], reverse: bool) -> List[Dict]:
    """Sort by distance and merge near-duplicate prices, keeping the first reason."""
    levels = sorted(levels, key=lambda x: x[0], reverse=reverse)
    out: List[Dict] = []
    for price, reason in levels:
        if any(abs(price - o["price"]) / max(price, 1) < _LEVEL_CLUSTER_PCT for o in out):
            continue
        out.append({"price": round(price, 2), "reason": reason})
    return out


# ---------------------------------------------------------------------------
# Bias & condition
# ---------------------------------------------------------------------------

def _bias(snapshot: Dict) -> str:
    ind = snapshot["indicators"]
    price = snapshot["current_price"]
    ema20, ema50, ema200 = ind.get("ema20"), ind.get("ema50"), ind.get("ema200")
    h4 = ind.get("h4_trend", "range")

    score = 0
    if ema20 is not None:
        score += 1 if price > ema20 else -1
    if ema20 is not None and ema50 is not None:
        score += 1 if ema20 > ema50 else -1
    if ema200 is not None:
        score += 1 if price > ema200 else -1
    score += {"bullish": 1, "bearish": -1}.get(h4, 0)

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "range"


def _market_condition(snapshot: Dict, bias: str) -> str:
    ind = snapshot["indicators"]
    price = snapshot["current_price"]
    atr = ind.get("atr14") or 0.0
    ema20, ema50 = ind.get("ema20"), ind.get("ema50")
    pdh = snapshot["previous_day"].get("high")
    pdl = snapshot["previous_day"].get("low")

    if pdh is not None and price > pdh:
        return "breakout"
    if pdl is not None and price < pdl:
        return "breakout"
    if bias in ("bullish", "bearish") and ema20 and ema50 and atr:
        if abs(ema20 - ema50) >= atr:
            return "trend"
    if atr and ema20 and abs(price - ema20) < 0.4 * atr:
        return "consolidation"
    return "range"


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------

def _levels(snapshot: Dict) -> Tuple[List[Dict], List[Dict]]:
    price = snapshot["current_price"]
    pd_ = snapshot["previous_day"]
    td = snapshot["today"]
    df = snapshot.get("_candles_df")

    labeled: List[Tuple[float, str, str]] = []  # (price, reason, side-hint unused)
    def add(val, reason):
        if val is not None:
            labeled.append((float(val), reason, ""))

    add(pd_.get("high"), "previous day high / rejection zone")
    add(pd_.get("low"), "previous day low / demand zone")
    add(td.get("asian_session_high"), "Asian session high / liquidity")
    add(td.get("asian_session_low"), "Asian session low / liquidity")
    add(td.get("current_day_high"), "intraday high")
    add(td.get("current_day_low"), "intraday low")
    add(td.get("open"), "current day open")

    if df is not None and len(df) > 12:
        sh, sl = _swings(df)
        for v in sh[-6:]:
            labeled.append((v, "recent swing high / liquidity", ""))
        for v in sl[-6:]:
            labeled.append((v, "recent swing low / demand", ""))

    resistance = [(p, r) for p, r, _ in labeled if p > price]
    support = [(p, r) for p, r, _ in labeled if p < price]
    res = _dedupe_levels(resistance, reverse=False)[:2]   # nearest above
    sup = _dedupe_levels(support, reverse=True)[:2]        # nearest below
    return res, sup


# ---------------------------------------------------------------------------
# Scenarios / plan / news
# ---------------------------------------------------------------------------

def _scenarios(bias: str, res: List[Dict], sup: List[Dict]) -> Tuple[str, str]:
    tf = config.intraday_tf_label
    r0 = res[0]["price"] if res else None
    s0 = sup[0]["price"] if sup else None
    if bias == "bullish":
        buy = (f"Buy dips into {s0} while price holds above the EMAs — confirm with a bullish {tf} close."
               if s0 else f"Buy dips that hold above the EMAs, confirmed by a bullish {tf} close.")
        sell = (f"Counter-trend only: sell if price rejects {r0} with a bearish {tf} close."
                if r0 else f"Counter-trend sells only on a clear rejection with a bearish {tf} close.")
    elif bias == "bearish":
        sell = (f"Sell rallies into {r0} while price stays below the EMAs — confirm with a bearish {tf} close."
                if r0 else f"Sell rallies that stay below the EMAs, confirmed by a bearish {tf} close.")
        buy = (f"Counter-trend only: buy if price reclaims {s0} with a bullish {tf} close back above it."
               if s0 else f"Counter-trend buys only on a clean reclaim with a bullish {tf} close.")
    else:  # range
        buy = (f"Buy near {s0} on a rejection wick + bullish {tf} close (range support)."
               if s0 else "Buy near range support on a bullish rejection close.")
        sell = (f"Sell near {r0} on a rejection wick + bearish {tf} close (range resistance)."
                if r0 else "Sell near range resistance on a bearish rejection close.")
    return buy, sell


def _invalidation(bias: str, res: List[Dict], sup: List[Dict]) -> str:
    tf = config.intraday_tf_label
    r0 = res[0]["price"] if res else None
    s0 = sup[0]["price"] if sup else None
    if bias == "bullish":
        return f"An {tf} close below {s0} invalidates the bullish plan." if s0 else \
               f"An {tf} close below key support invalidates the bullish plan."
    if bias == "bearish":
        return f"An {tf} close above {r0} invalidates the bearish plan." if r0 else \
               f"An {tf} close above key resistance invalidates the bearish plan."
    if r0 and s0:
        return f"A decisive {tf} close beyond {r0} or below {s0} breaks the range."
    return f"A decisive {tf} close beyond the range extremes changes the plan."


def _preferred_plan(bias: str, condition: str) -> str:
    if condition == "breakout":
        return "wait for breakout"
    if bias == "bullish":
        return "buy dips"
    if bias == "bearish":
        return "sell rallies"
    return "range trade only"


def _news_risk(upcoming: List[Dict]) -> Tuple[str, str]:
    """Return (risk_level, summary) from upcoming USD events for the session."""
    if not upcoming:
        return "none", "No medium or high-impact USD news in the session window."
    has_high = any(e.get("impact") == "high" or e.get("is_major") for e in upcoming)
    has_med = any(e.get("impact") == "medium" for e in upcoming)
    risk = "high" if has_high else ("medium" if has_med else "none")
    nearest = upcoming[:2]
    parts = [f"{e.get('impact','').upper()} {e['event_name']} at {e.get('time_sgt','')} SGT"
             for e in nearest]
    summary = "; ".join(parts) + ". Signals will pause around the release." if parts else \
              "No medium or high-impact USD news in the session window."
    return risk, summary


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_plan(snapshot: Dict, upcoming_events: Optional[List[Dict]] = None) -> Dict:
    upcoming_events = upcoming_events or []
    bias = _bias(snapshot)
    condition = _market_condition(snapshot, bias)
    res, sup = _levels(snapshot)
    buy, sell = _scenarios(bias, res, sup)
    invalidation = _invalidation(bias, res, sup)
    plan = _preferred_plan(bias, condition)
    news_risk, news_summary = _news_risk(upcoming_events)

    date_sgt = datetime.now(config.tz).strftime("%d %b %Y")
    member_message = templates.intraday_plan(
        date_sgt=date_sgt,
        bias=bias.capitalize(),
        market_condition=condition.capitalize(),
        key_resistance=res,
        key_support=sup,
        buy_condition=buy,
        sell_condition=sell,
        invalidation=invalidation,
        news_summary=news_summary,
        preferred_plan=plan,
    )

    result = {
        "analysis_type": "intraday_plan",
        "instrument": snapshot["instrument"],
        "timeframe": config.intraday_tf_label,
        "bias": bias,
        "market_condition": condition,
        "key_resistance": res,
        "key_support": sup,
        "buy_scenario": {"valid": True, "condition": buy},
        "sell_scenario": {"valid": True, "condition": sell},
        "invalidation": invalidation,
        "news_risk": news_risk,
        "preferred_plan": plan,
        "member_message": member_message,
    }
    log.info("Intraday plan: bias=%s condition=%s plan=%s news_risk=%s res=%s sup=%s",
             bias, condition, plan, news_risk,
             [r["price"] for r in res], [s["price"] for s in sup])
    return result


def to_db_row(snapshot: Dict, plan: Dict, chart_path: Optional[str]) -> Dict:
    """Shape an intraday_analyses row (snapshot's in-memory df is stripped)."""
    raw = {k: v for k, v in snapshot.items() if k != "_candles_df"}
    return {
        "instrument": snapshot["instrument"],
        "analysis_time_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "analysis_time_sgt": datetime.now(config.tz).strftime("%Y-%m-%d %H:%M:%S"),
        "timeframe": config.intraday_tf_label,
        "chart_path": chart_path,
        "raw_market_data_json": json.dumps(raw, default=str),
        "bias": plan["bias"],
        "market_condition": plan["market_condition"],
        "plan_json": json.dumps(plan, default=str),
        "member_message": plan["member_message"],
    }
