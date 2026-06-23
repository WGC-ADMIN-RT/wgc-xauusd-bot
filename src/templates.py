"""Message templates — reproduced exactly from the Developer Workflow Spec.

Every Phase 1 message format from the PDF is implemented here verbatim:
  Task 1: daily outlook (+ no-events), 1-hour alert, 15-min warning,
          post-release, "actual not available".
  Task 2: intraday plan.
Plus the admin alert for calendar failures.

All user-facing times are SGT. Inputs are plain dicts/values; callers pass
already-SGT-formatted time strings.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

IMPACT_EMOJI = {"high": "🔴", "medium": "🟠", "low": "⚪"}
BRAND = "Wings Gold Club"


def impact_emoji(impact: str) -> str:
    return IMPACT_EMOJI.get((impact or "").lower(), "⚪")


def _group_by_time(events: Sequence[dict]) -> List[List[dict]]:
    """Group consecutive same-time events (events arrive pre-sorted by release time).

    Grouping on consecutive equal time strings is safe even when a clock time recurs
    on a later day: those events are non-adjacent in the sorted list, so they fall
    into separate groups."""
    groups: List[List[dict]] = []
    current: List[dict] = []
    key = object()
    for ev in events:
        t = ev.get("time_sgt")
        if t != key:
            if current:
                groups.append(current)
            current, key = [ev], t
        else:
            current.append(ev)
    if current:
        groups.append(current)
    return groups


def _fmt(value) -> str:
    """Render a value, falling back to a dash when missing."""
    if value is None or str(value).strip() == "":
        return "—"
    return str(value)


# ----------------------------------------------------------------------------
# Task 1 — USD News Automation
# ----------------------------------------------------------------------------

def daily_outlook(date_sgt: str, events: Sequence[dict]) -> str:
    """🗞 Daily news outlook (12:00 PM SGT).

    Each event dict: time_sgt, impact, event_name, forecast, previous, short_reason.
    Events are expected pre-sorted; same-time events simply appear consecutively.
    """
    if not events:
        return daily_outlook_no_events(date_sgt)

    lines = [
        f"🗞 USD News Outlook — {date_sgt} SGT",
        "",
        "Today's tracked USD events for XAUUSD:",
    ]
    # Forex-Factory style: one time header, the same-time events listed under it.
    for group in _group_by_time(events):
        lines.append("")
        lines.append(f"{group[0]['time_sgt']} — USD")
        for ev in group:
            lines.append(
                f"{impact_emoji(ev['impact'])} {ev['event_name']} "
                f"(Forecast: {_fmt(ev.get('forecast'))} | Previous: {_fmt(ev.get('previous'))})"
            )
    lines += [
        "",
        "Risk reminder:",
        "High-impact news can cause spread widening, slippage, fake breakouts, and "
        "fast reversals. Signals will pause near major releases.",
        "",
        f"— {BRAND}",
    ]
    return "\n".join(lines)


def daily_outlook_no_events(date_sgt: str) -> str:
    return (
        f"🗞 USD News Outlook — {date_sgt} SGT\n\n"
        "No medium or high-impact USD news scheduled in the next 24 hours.\n\n"
        "XAUUSD automation will focus mainly on technical intraday structure unless "
        "unscheduled USD headlines appear.\n\n"
        f"— {BRAND}"
    )


def alert_1h(event: dict) -> str:
    """⚠️ One alert per event group, 1 hour before."""
    return (
        "⚠️ USD News Alert — 1 Hour Before\n\n"
        f"{event['time_sgt']} SGT — {impact_emoji(event['impact'])} {event['event_name']}\n"
        f"Forecast: {_fmt(event.get('forecast'))}\n"
        f"Previous: {_fmt(event.get('previous'))}\n\n"
        "Potential XAUUSD impact:\n"
        "- Stronger USD data usually pressures XAUUSD lower.\n"
        "- Weaker USD data usually supports XAUUSD higher.\n"
        "- Mixed data = wait for price confirmation.\n\n"
        "Automation note:\n"
        "New signals will be more selective before this release."
    )


def warning_15m(event: dict) -> str:
    """🚨 Final warning, 15 minutes before."""
    return (
        "🚨 Final USD News Warning — 15 Minutes\n\n"
        f"{event['time_sgt']} SGT — {impact_emoji(event['impact'])} {event['event_name']}\n\n"
        f"Forecast: {_fmt(event.get('forecast'))}\n"
        f"Previous: {_fmt(event.get('previous'))}\n\n"
        "XAUUSD warning:\n"
        "Spreads and volatility may increase. Avoid chasing candles before the release.\n\n"
        "Signal automation:\n"
        "Paused until after the release and confirmation candle."
    )


def post_release(event: dict, reaction: dict) -> str:
    """📊 Post-release Actual vs Forecast/Previous + XAUUSD impact.

    reaction dict: usd_bias_summary, price_before, current_price, price_change,
                   xauusd_impact_summary, signal_status.
    """
    return (
        f"📊 USD News Released — {event['event_name']}\n\n"
        f"Actual: {_fmt(event.get('actual'))}\n"
        f"Forecast: {_fmt(event.get('forecast'))}\n"
        f"Previous: {_fmt(event.get('previous'))}\n\n"
        "Data read:\n"
        f"{reaction.get('usd_bias_summary', '')}\n\n"
        "XAUUSD reaction:\n"
        f"Before release: {_fmt(reaction.get('price_before'))}\n"
        f"Current: {_fmt(reaction.get('current_price'))}\n"
        f"Move: {_fmt(reaction.get('price_change'))}\n\n"
        "Impact summary:\n"
        f"{reaction.get('xauusd_impact_summary', '')}\n\n"
        "Signal automation:\n"
        f"{reaction.get('signal_status', 'Signal automation remains paused until the next M5/M15 candle confirms direction.')}"
    )


def actual_unavailable() -> str:
    return "Actual value not available yet from data source. Update will be sent once confirmed."


# ----------------------------------------------------------------------------
# Task 2 — Intraday Analysis
# ----------------------------------------------------------------------------

def intraday_plan(
    date_sgt: str,
    bias: str,
    market_condition: str,
    key_resistance: List[dict],
    key_support: List[dict],
    buy_condition: str,
    sell_condition: str,
    invalidation: str,
    news_summary: str,
    preferred_plan: str,
) -> str:
    """📈 XAUUSD Intraday Plan (2:30 PM SGT). Levels: list of {price, reason}."""

    def _levels(levels: List[dict]) -> str:
        if not levels:
            return "1. —"
        return "\n".join(
            f"{i}. {lv['price']} — {lv['reason']}" for i, lv in enumerate(levels[:3], 1)
        )

    return (
        f"📈 XAUUSD Intraday Plan — {date_sgt} SGT\n\n"
        f"Current bias: {bias}\n"
        f"Market condition: {market_condition}\n\n"
        "Key resistance:\n"
        f"{_levels(key_resistance)}\n\n"
        "Key support:\n"
        f"{_levels(key_support)}\n\n"
        "Buy scenario:\n"
        f"{buy_condition}\n\n"
        "Sell scenario:\n"
        f"{sell_condition}\n\n"
        "Invalidation:\n"
        f"{invalidation}\n\n"
        "USD news risk:\n"
        f"{news_summary}\n\n"
        "Plan:\n"
        f"{preferred_plan}\n\n"
        "Note: This is the trading plan. Entry, TP, and SL will only be sent if the "
        "signal engine confirms a valid setup."
    )


# ----------------------------------------------------------------------------
# Admin / error
# ----------------------------------------------------------------------------

def admin_alert_calendar() -> str:
    return (
        "⚠️ Admin Alert: Economic calendar data unavailable. News automation "
        "degraded. Signal engine has switched to conservative mode."
    )


def admin_alert_market_data() -> str:
    return (
        "⚠️ Admin Alert: Market data unavailable. Intraday analysis will not be "
        "published until data is restored."
    )
