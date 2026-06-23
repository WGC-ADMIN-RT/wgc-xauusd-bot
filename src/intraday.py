"""Intraday Analysis (Task 2) — AI-first daily plan with a deterministic fallback.

Consumes a market_data snapshot (+ upcoming USD news) and produces the structured
plan behind the 📈 intraday message. Two engines:

  * AI (default): Claude as a 20-yr XAUUSD trader (M5 execution, H1 bias) emits key
    ZONES (price ranges) + 4-5 game plans via tool-use — see ai_intraday.py.
  * Rule-based fallback: the original deterministic logic (bias, S/R levels, buy/sell
    scenarios). Used when AI is disabled, unkeyed, or the API call fails, so the 2:30
    job never goes dark.

Both engines return the same result contract (bias, market_condition, member_message,
preferred_plan, plan_json-able), so run_intraday.py / to_db_row are engine-agnostic.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import config
import ai_intraday
import templates

log = logging.getLogger("intraday")

# Two prices within this fraction of each other are treated as the same level.
_LEVEL_CLUSTER_PCT = 0.0012  # ~0.12%


# ---------------------------------------------------------------------------
# Swing levels
# ---------------------------------------------------------------------------

def _swings(candles: List[Dict], window: int = 5) -> Tuple[List[float], List[float]]:
    highs: List[float] = []
    lows: List[float] = []
    h = [c["high"] for c in candles]
    l = [c["low"] for c in candles]
    n = len(candles)
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
    ema20 = ind.get("m5_ema20") or ind.get("ema20")
    ema50 = ind.get("m5_ema50") or ind.get("ema50")
    ema200 = ind.get("m5_ema200") or ind.get("ema200")
    h1 = (snapshot.get("h1_structure") or {}).get("trend") or ind.get("h1_trend") or "range"

    score = 0
    if ema20 is not None:
        score += 1 if price > ema20 else -1
    if ema20 is not None and ema50 is not None:
        score += 1 if ema20 > ema50 else -1
    if ema200 is not None:
        score += 1 if price > ema200 else -1
    score += {"bullish": 1, "bearish": -1}.get(h1, 0)

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "range"


def _market_condition(snapshot: Dict, bias: str) -> str:
    ind = snapshot["indicators"]
    price = snapshot["current_price"]
    atr = ind.get("m5_atr14") or ind.get("atr14") or 0.0
    ema20 = ind.get("m5_ema20") or ind.get("ema20")
    ema50 = ind.get("m5_ema50") or ind.get("ema50")
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
    candles = snapshot.get("_candles")

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

    if candles and len(candles) > 12:
        sh, sl = _swings(candles)
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
    """Dispatch to the AI engine when configured, else the rule-based fallback."""
    upcoming_events = upcoming_events or []
    if config.intraday_ai_enabled and config.anthropic_api_key:
        try:
            ai = ai_intraday.generate(snapshot, upcoming_events)
            if ai:
                return _ai_plan_result(snapshot, ai, upcoming_events)
        except Exception:
            log.exception("AI intraday failed — falling back to rule-based plan")
    return _rule_based_plan(snapshot, upcoming_events)


def _normalize_zones(zones: List[Dict]) -> List[Dict]:
    """Ensure each zone has low, high, label for the member template."""
    out = []
    for z in zones or []:
        if z.get("low") is None or z.get("high") is None:
            continue
        out.append({
            "low": z["low"],
            "high": z["high"],
            "label": (z.get("label") or z.get("reason") or "").strip(),
        })
    return out


def _zone_range(z: Dict) -> str:
    lo, hi = float(z["low"]), float(z["high"])
    if lo > hi:
        lo, hi = hi, lo
    return f"{lo:.2f}".rstrip("0").rstrip(".") + "-" + f"{hi:.2f}".rstrip("0").rstrip(".")


def _rule_based_gameplans(
    bias: str, demand: List[Dict], supply: List[Dict], snapshot: Dict,
) -> List[Dict]:
    """Four conditional game plans in trader prose (fallback when AI is unavailable)."""
    tf = config.intraday_tf_label
    h1 = (snapshot.get("h1_structure") or {})
    struct_h, struct_l = h1.get("structure_high"), h1.get("structure_low")
    plans: List[Dict] = []

    if demand:
        z = demand[0]
        rng = _zone_range(z)
        tag = z.get("label") or "demand"
        if bias == "bullish":
            plans.append({"text": (
                f"Our {tag} zone at {rng} is today's primary demand. If price holds "
                f"the zone and prints a {tf} bullish CHoCH, look for longs on a pullback "
                f"to an {tf} FVG while H1 stays {bias}; a {tf} close below the zone "
                f"invalidates the long idea."
            )})
        else:
            plans.append({"text": (
                f"Our {tag} zone at {rng} is a pivotal decision area. If price sweeps "
                f"into the zone and rejects with a {tf} bearish CHoCH, look for shorts "
                f"toward the next demand pocket; acceptance below the zone opens continuation "
                f"shorts in line with H1 {bias}."
            )})

    if supply:
        z = supply[0]
        rng = _zone_range(z)
        tag = z.get("label") or "supply"
        plans.append({"text": (
            f"At our {tag} zone ({rng}), watch for a fade if price runs into supply and "
            f"prints a {tf} bearish CHoCH — target the nearest demand zone below. "
            f"A clean {tf} close above the zone targets the next H1 supply shelf."
        )})

    if struct_l:
        plans.append({"text": (
            f"Potential longs if price breaks and holds above the prior H1 structure low "
            f"at {struct_l} with a {tf} bullish CHoCH; wait for a pullback to an {tf} "
            f"FVG before entry while H1 bias remains {bias}."
        )})

    if struct_h:
        plans.append({"text": (
            f"Potential shorts if price rejects the H1 structure high at {struct_h} with "
            f"a {tf} bearish CHoCH; scale into supply on the retest and target the "
            f"nearest demand zone."
        )})

    if len(plans) < 4 and len(demand) > 1:
        z = demand[1]
        rng = _zone_range(z)
        plans.append({"text": (
            f"Secondary demand at {rng}: range long only on a sharp {tf} rejection wick "
            f"+ bullish close; skip if H1 momentum is strongly against the trade."
        )})

    return plans[: max(4, min(5, config.intraday_gameplans))]


def _ai_plan_result(snapshot: Dict, ai: Dict, upcoming_events: List[Dict]) -> Dict:
    """Shape the AI tool output into the standard plan contract + member message."""
    bias = str(ai.get("h1_bias") or ai.get("market_bias") or "range").lower()
    demand = _normalize_zones(ai.get("demand_zones") or ai.get("support_zones"))
    supply = _normalize_zones(ai.get("supply_zones") or ai.get("resistance_zones"))
    if not demand:
        demand = _normalize_zones((snapshot.get("zone_hints") or {}).get("demand"))
    if not supply:
        supply = _normalize_zones((snapshot.get("zone_hints") or {}).get("supply"))
    gameplans = ai.get("gameplans") or []
    news_risk_level, news_default = _news_risk(upcoming_events)
    news_summary = ai.get("news_risk") or news_default
    h1_context = ai.get("h1_context") or ""

    date_sgt = datetime.now(config.tz).strftime("%A, %d %b %Y")
    member_message = templates.intraday_plan_ai(
        date_sgt=date_sgt,
        h1_bias=bias,
        h1_context=h1_context,
        demand_zones=demand,
        supply_zones=supply,
        gameplans=gameplans,
        news_summary=news_summary,
    )

    result = {
        "analysis_type": "intraday_gameplan",
        "instrument": snapshot["instrument"],
        "timeframe": "M5",
        "bias_timeframe": "H1",
        "engine": "ai",
        "ai_model": config.intraday_ai_model,
        "bias": bias,
        "h1_context": h1_context,
        "demand_zones": demand,
        "supply_zones": supply,
        "gameplans": gameplans,
        "news_risk": news_risk_level,
        "preferred_plan": f"H1 {bias} — M5 gameplan",
        "member_message": member_message,
    }
    log.info("AI intraday plan: h1=%s demand=%d supply=%d gameplans=%d",
             bias, len(demand), len(supply), len(gameplans))
    return result


def _rule_based_plan(snapshot: Dict, upcoming_events: Optional[List[Dict]] = None) -> Dict:
    upcoming_events = upcoming_events or []
    bias = _bias(snapshot)
    hints = snapshot.get("zone_hints") or {}
    demand = _normalize_zones(hints.get("demand"))
    supply = _normalize_zones(hints.get("supply"))
    if not demand and not supply:
        res, sup = _levels(snapshot)
        atr = snapshot["indicators"].get("m5_atr14") or snapshot["indicators"].get("atr14") or 1.0
        for lv in sup:
            half = max(atr * 0.15, lv["price"] * 0.001)
            demand.append({"low": round(lv["price"] - half, 2), "high": round(lv["price"] + half, 2),
                           "label": lv["reason"]})
        for lv in res:
            half = max(atr * 0.15, lv["price"] * 0.001)
            supply.append({"low": round(lv["price"] - half, 2), "high": round(lv["price"] + half, 2),
                           "label": lv["reason"]})
    gameplans = _rule_based_gameplans(bias, demand, supply, snapshot)
    news_risk, news_summary = _news_risk(upcoming_events)
    h1 = snapshot.get("h1_structure") or {}
    h1_context = (
        f"H1 is {bias} with structure high {h1.get('structure_high') or '—'} and "
        f"structure low {h1.get('structure_low') or '—'}; execute on M5 only."
    )

    date_sgt = datetime.now(config.tz).strftime("%A, %d %b %Y")
    member_message = templates.intraday_plan_ai(
        date_sgt=date_sgt,
        h1_bias=bias,
        h1_context=h1_context,
        demand_zones=demand,
        supply_zones=supply,
        gameplans=gameplans,
        news_summary=news_summary,
    )

    result = {
        "analysis_type": "intraday_gameplan",
        "instrument": snapshot["instrument"],
        "timeframe": "M5",
        "bias_timeframe": "H1",
        "engine": "rule_based",
        "bias": bias,
        "h1_context": h1_context,
        "demand_zones": demand,
        "supply_zones": supply,
        "gameplans": gameplans,
        "news_risk": news_risk,
        "preferred_plan": f"H1 {bias} — M5 gameplan",
        "member_message": member_message,
    }
    log.info("Rule-based gameplan: h1=%s demand=%d supply=%d gameplans=%d",
             bias, len(demand), len(supply), len(gameplans))
    return result


def to_db_row(snapshot: Dict, plan: Dict, chart_path: Optional[str]) -> Dict:
    """Shape an intraday_analyses row (snapshot's in-memory candles are stripped)."""
    raw = {k: v for k, v in snapshot.items() if not k.startswith("_")}
    return {
        "instrument": snapshot["instrument"],
        "analysis_time_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "analysis_time_sgt": datetime.now(config.tz).strftime("%Y-%m-%d %H:%M:%S"),
        "timeframe": config.intraday_tf_label,
        "chart_path": chart_path,
        "raw_market_data_json": json.dumps(raw, default=str),
        "bias": plan["bias"],
        "market_condition": plan.get("market_condition") or plan.get("bias"),
        "plan_json": json.dumps(plan, default=str),
        "member_message": plan["member_message"],
    }
