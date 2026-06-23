"""AI intraday analysis — Claude as a 20-year XAUUSD trader (M5 execution, H1 bias).

Structured output via tool-use: supply/demand zones as price RANGES + 4-5 prose game
plans matching the member Telegram format (chart screenshot + numbered Gameplan).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from config import config

log = logging.getLogger("ai_intraday")

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = 90
_MAX_RETRIES = 4
_RETRY_STATUS = {429, 500, 502, 503, 504, 529}
_TOOL_NAME = "publish_intraday_plan"

_SYSTEM = (
    "You are a professional XAUUSD (spot gold) trader with 20 years of experience.\n"
    "You analyse ONLY the M5 (5-minute) and H1 (1-hour) charts — no other timeframes.\n"
    "M5 is for execution and triggers; H1 sets directional bias and market structure.\n\n"
    "You think in SUPPLY and DEMAND ZONES — never single prices. Every key level is a "
    "RANGE (low–high), e.g. 4110–4118 or 4331.5–4323.7. Label zones with trader terms "
    "where appropriate: OB (order block), FVG (fair value gap), LQ (liquidity), "
    "SBR (support-becomes-resistance), inducement, structure high/low.\n\n"
    f"Produce exactly {config.intraday_gameplans} numbered GAME PLANS (4 to 5). "
    "Each game plan is one flowing paragraph of conditional logic — IF price does X at "
    "zone Y on M5 with H1 context Z, THEN look for long/short, invalidation, target. "
    "Cover continuation, pullback, fade at supply, sweep of liquidity, and breakout "
    "scenarios. Reference M5 CHoCH / structure breaks and H1 bias — not M1 or M15.\n\n"
    "Use zone_hints, h1_structure, and indicators from the JSON payload. Tighten ranges "
    "to realistic gold width (~0.5–2.0 pts for M5, wider for H1 supply/demand).\n\n"
    "Example game plan tone:\n"
    "'Our LQ zone at 4331.5–4323.7 serves as a pivotal demand zone. If price respects "
    "it and prints an M5 bullish CHoCH, look for longs on a pullback to an M5 FVG; "
    "if price dumps through, wait for an M5 bearish CHoCH and fade into H1 supply.'\n\n"
    f"Respond ONLY by calling the {_TOOL_NAME} tool."
)

_ZONE_SCHEMA = {
    "type": "object",
    "properties": {
        "low": {"type": "number", "description": "Lower bound of the zone"},
        "high": {"type": "number", "description": "Upper bound of the zone"},
        "label": {"type": "string", "description": "Short tag, e.g. 'LQ', 'H1 OB', 'M5 FVG'"},
    },
    "required": ["low", "high", "label"],
}

_TOOL = {
    "name": _TOOL_NAME,
    "description": "Publish the XAUUSD M5/H1 intraday game plan for Telegram.",
    "input_schema": {
        "type": "object",
        "properties": {
            "h1_bias": {
                "type": "string",
                "enum": ["bullish", "bearish", "range"],
                "description": "H1 directional bias",
            },
            "h1_context": {
                "type": "string",
                "description": "1–2 sentences: H1 structure, trend, key breaks",
            },
            "demand_zones": {
                "type": "array", "minItems": 2, "maxItems": 4, "items": _ZONE_SCHEMA,
                "description": "Support / demand zones below or around price",
            },
            "supply_zones": {
                "type": "array", "minItems": 2, "maxItems": 4, "items": _ZONE_SCHEMA,
                "description": "Resistance / supply zones above or around price",
            },
            "gameplans": {
                "type": "array", "minItems": 4, "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "One complete game plan paragraph. Mention the zone as a "
                                "range, M5 trigger, H1 context, invalidation, and target."
                            ),
                        },
                    },
                    "required": ["text"],
                },
            },
            "news_risk": {
                "type": "string",
                "description": "USD news risk for the session and impact on plans",
            },
        },
        "required": ["h1_bias", "h1_context", "demand_zones", "supply_zones",
                     "gameplans", "news_risk"],
    },
}


def _user_payload(snapshot: Dict, upcoming_events: List[Dict]) -> str:
    data = {k: v for k, v in snapshot.items() if not k.startswith("_")}
    data["upcoming_usd_news"] = upcoming_events
    return (
        "Build today's XAUUSD intraday plan from this M5/H1 data. "
        "Zones must be ranges; output 4–5 game plans in trader prose.\n\n"
        + json.dumps(data, default=str, indent=2)
    )


def _post(body: bytes, api_key: str) -> Dict:
    req = urllib.request.Request(
        _API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        },
    )
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRY_STATUS or attempt == _MAX_RETRIES:
                detail = exc.read().decode("utf-8", "replace")[:500]
                log.error("Anthropic HTTP %s: %s", exc.code, detail)
                raise
            wait = min(2 ** attempt, 20)
            log.warning("Anthropic %s — retry in %ss (attempt %d)", exc.code, wait, attempt + 1)
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                raise
            wait = min(2 ** attempt, 20)
            log.warning("Anthropic network error (%s) — retry in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Anthropic call failed after retries: {last_exc}")


def _extract_tool_input(payload: Dict) -> Optional[Dict]:
    for block in payload.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
            return block.get("input")
    return None


def generate(snapshot: Dict, upcoming_events: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Return the AI structured intraday plan, or None on any failure."""
    api_key = config.anthropic_api_key
    if not api_key:
        log.info("ANTHROPIC_API_KEY not set — skipping AI intraday")
        return None

    body = json.dumps({
        "model": config.intraday_ai_model,
        "max_tokens": 4096,
        "system": _SYSTEM,
        "tools": [_TOOL],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        "messages": [{"role": "user", "content": _user_payload(snapshot, upcoming_events or [])}],
    }).encode("utf-8")

    try:
        payload = _post(body, api_key)
    except Exception:
        log.exception("Anthropic intraday call failed")
        return None

    plan = _extract_tool_input(payload)
    if not plan:
        log.error("No %s tool_use in response: stop_reason=%s",
                  _TOOL_NAME, payload.get("stop_reason"))
        return None

    usage = payload.get("usage", {})
    log.info("AI intraday ok (model=%s in=%s out=%s gameplans=%d)",
             config.intraday_ai_model, usage.get("input_tokens"),
             usage.get("output_tokens"), len(plan.get("gameplans", [])))
    return plan
