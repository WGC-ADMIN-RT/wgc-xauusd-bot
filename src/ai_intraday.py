"""AI intraday analysis — Claude as a 20-year XAUUSD trader (M5 execution, H1 bias).

Structured output via tool-use: supply/demand zones as price RANGES + 4-5 SHORT
game plans (Orient FX style) for Telegram.
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
    "You analyse ONLY M5 (execution) and H1 (bias/structure).\n\n"
    "ZONES: supply/demand as RANGES (low–high). Tags: OB, FVG, LQ, SBR, RBS, flip, "
    "structure high/low.\n\n"
    f"Produce exactly {config.intraday_gameplans} SHORT game plans (4–5) — Orient FX style.\n"
    "Each plan is ONE line (max TWO short sentences only for the single pivotal/flip zone).\n\n"
    "Format (use these openers):\n"
    "  • Potential buys at our {tag} zone at {low}-{high}\n"
    "  • Potential sells at our {tag} zone at {low}-{high}\n"
    "  • Medium risk buys/sells at our {tag} zone at {low}-{high}\n"
    "  • Potential buys/sells if price breaks {level}\n"
    "  • Our {tag} zone at {low}-{high} is pivotal — if price rejects with M5 CHoCH "
    "look for {direction}; break the other way targets the next zone.\n\n"
    "Rules:\n"
    "  • Keep game plans SHORT — no long paragraphs, no repeating the zone lists.\n"
    "  • Reference zones from demand_zones/supply_zones already listed in the message.\n"
    "  • M5 triggers only (CHoCH, break, reject, sweep, inducement).\n"
    "  • h1_context: 2–3 sentences max (bias + structure high/low + EMA context).\n"
    "  • Chart context: member chart shows TWO full Asian sessions (08:00–16:00 SGT).\n\n"
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
                "description": "2–3 sentences: H1 structure, trend, key EMA/levels",
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
                                "One SHORT game plan line (Orient FX style). "
                                "Start with Potential/Medium risk buys or sells; "
                                "include zone tag + price range."
                            ),
                        },
                    },
                    "required": ["text"],
                },
            },
            "news_risk": {
                "type": "string",
                "description": "One short line on USD news risk for the session",
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
        "Build today's XAUUSD intraday plan. Zones as ranges; 4–5 SHORT one-line game "
        "plans (Orient FX style). Do not write long paragraphs.\n\n"
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
