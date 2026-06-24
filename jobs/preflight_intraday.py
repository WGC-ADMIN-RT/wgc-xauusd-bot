#!/usr/bin/env python
"""Intraday pre-flight — 2:30 PM SGT gate, two Asian sessions, chart + game plan.

Run on the server (needs FMP, Chart-IMG, DB, optional Anthropic):

    .venv/bin/python jobs/preflight_intraday.py
    .venv/bin/python jobs/preflight_intraday.py --fix      # rm premature intraday marker
    .venv/bin/python jobs/preflight_intraday.py --test-send  # TEST intraday to Telegram
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import bootstrap  # noqa: E402

bootstrap.init()

import calendar_service  # noqa: E402
import charts  # noqa: E402
import db  # noqa: E402
import intraday  # noqa: E402
import market_data  # noqa: E402
import schedule_guard  # noqa: E402
import telegram_client  # noqa: E402
from config import config  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGS = os.path.join(ROOT, "logs")
TARGET = config.intraday_analysis_sgt  # "14:30"
WINDOW_MIN = 10


def _sgt_now() -> datetime:
    return datetime.now(config.tz)


def _marker_path(today: str) -> str:
    return os.path.join(LOGS, f".intraday_{today}")


def _target_today(now: datetime) -> datetime:
    hh, mm = map(int, TARGET.split(":"))
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _check_config() -> list[str]:
    lines: list[str] = []
    base = config.validate()
    for p in base:
        lines.append(f"FAIL  {p}")

    if config.intraday_tf != "5min":
        lines.append(f"WARN  INTRADAY_TF={config.intraday_tf} — member spec is M5 (set 5min)")
    else:
        lines.append("OK    INTRADAY_TF=5min (M5)")

    if config.chartimg_api_key:
        lines.append("OK    CHARTIMG_API_KEY set")
    else:
        lines.append("WARN  CHARTIMG_API_KEY missing — chart will be skipped")

    if config.chartimg_layout_id:
        lines.append(f"OK    CHARTIMG_LAYOUT_ID={config.chartimg_layout_id} (TradingView layout)")
    else:
        lines.append("OK    no layout id — advanced chart + range API")

    sym = config.chartimg_symbol
    if sym and ":" in sym:
        lines.append(f"OK    CHARTIMG_SYMBOL={sym}")
    elif sym:
        lines.append(f"WARN  CHARTIMG_SYMBOL={sym} — use EXCHANGE:SYMBOL (e.g. OANDA:XAUUSD)")
    else:
        lines.append("WARN  CHARTIMG_SYMBOL empty — defaulting to OANDA:XAUUSD")

    if config.chartimg_layout_id and not config.chartimg_tv_session:
        lines.append(
            "WARN  CHARTIMG_TV_SESSION_ID missing — layout may ignore symbol override "
            "and show the broker saved in TradingView (e.g. FOREX.com)"
        )
    elif config.chartimg_tv_session:
        lines.append("OK    CHARTIMG_TV_SESSION_ID set (symbol override enabled)")

    if config.intraday_ai_enabled:
        if config.anthropic_api_key:
            lines.append(f"OK    AI intraday on ({config.intraday_ai_model}, {config.intraday_gameplans} plans)")
        else:
            lines.append("WARN  INTRADAY_AI_ENABLED but ANTHROPIC_API_KEY missing — rule-based fallback")
    else:
        lines.append("OK    AI disabled — rule-based game plans")

    if not lines:
        lines.append("OK    config")
    elif not any(l.startswith("FAIL") for l in lines):
        if not any(l.startswith("WARN") for l in lines):
            pass  # all OK lines already
    return lines


def _check_schedule(now: datetime, today: str) -> list[str]:
    lines: list[str] = []
    target = _target_today(now)
    path = _marker_path(today)
    due = schedule_guard.daily_due(TARGET, WINDOW_MIN, "intraday")

    lines.append(f"OK    fire window: {TARGET}–{TARGET[:2]}:{int(TARGET[3:]) + WINDOW_MIN:02d} SGT ({WINDOW_MIN} min)")
    lines.append(f"      cron: */5 * * * * run_intraday.py (self-gates on SGT)")

    if os.path.exists(path):
        if now < target:
            lines.append(
                f"WARN  intraday marker exists BEFORE {TARGET} SGT — live send will be SKIPPED "
                f"(run --fix or rm {path})"
            )
        else:
            lines.append(f"OK    intraday already ran today ({path})")
    else:
        if now < target:
            mins = int((target - now).total_seconds() // 60)
            lines.append(f"OK    not yet sent today — due in ~{mins} min at {TARGET} SGT")
        elif now < target + timedelta(minutes=WINDOW_MIN):
            lines.append(f"OK    inside fire window now — due={due}")
        else:
            lines.append(
                f"WARN  {TARGET} window passed with no marker — cron may have missed today's send"
            )
    return lines


def _check_snapshot(snapshot: dict) -> list[str]:
    lines: list[str] = []
    sessions = snapshot.get("asian_sessions") or []
    chart_range = snapshot.get("chart_range")

    lines.append(f"OK    price={snapshot.get('current_price')} spread={snapshot.get('spread')}")
    lines.append(f"OK    M5 candles loaded: {snapshot.get('candles_in_screenshot')}")

    if len(sessions) >= 2:
        for s in sessions[-2:]:
            lines.append(
                f"OK    Asian session {s['date_sgt']}: {s['low']}–{s['high']} SGT 08:00–16:00"
            )
    else:
        lines.append(
            f"FAIL  only {len(sessions)} Asian session(s) in snapshot — chart needs 2 "
            "(check FMP M5 history depth)"
        )

    if chart_range:
        hours = charts._chart_range_hours(chart_range)  # noqa: SLF001 — audit helper
        span = f"{hours:.1f}h" if hours else "?"
        lines.append(f"OK    chart_range spans {span} ({chart_range['from']} -> {chart_range['to']})")
    else:
        lines.append("FAIL  chart_range is None — Chart-IMG cannot pin two Asian sessions")

    td = snapshot.get("today") or {}
    if td.get("asian_session_high") is not None:
        lines.append(
            f"OK    today Asian box: {td.get('asian_session_low')}–{td.get('asian_session_high')}"
        )
    return lines


def _check_chart_payload(snapshot: dict) -> list[str]:
    lines: list[str] = []
    if not config.chartimg_api_key:
        lines.append("SKIP  chart render (no CHARTIMG_API_KEY)")
        return lines

    if config.chartimg_layout_id:
        payload = charts._layout_chart_payload(snapshot)  # noqa: SLF001
        mode = "layout zoom heuristics"
    else:
        payload = {
            "symbol": config.chartimg_symbol,
            "interval": charts._interval(),  # noqa: SLF001
        }
        charts._apply_chart_view(payload, snapshot, layout=False)  # noqa: SLF001
        mode = "advanced-chart range API"

    view_keys = [k for k in ("range", "zoomOut", "zoomIn", "moveLeft", "moveRight", "resetZoom")
                 if payload.get(k)]
    if view_keys:
        lines.append(f"OK    chart view via {mode}: {', '.join(view_keys)}")
        if payload.get("range"):
            lines.append(f"      range.from={payload['range'].get('from')}")
            lines.append(f"      range.to={payload['range'].get('to')}")
    elif snapshot.get("chart_range"):
        lines.append("WARN  chart_range computed but no view keys in payload — screenshot may not show 2 sessions")
    else:
        lines.append("FAIL  no chart view — two Asian sessions will not appear")
    return lines


def _check_plan(plan: dict) -> list[str]:
    lines: list[str] = []
    engine = plan.get("engine", "?")
    bias = plan.get("bias", "?")
    demand = plan.get("demand_zones") or []
    supply = plan.get("supply_zones") or []
    gps = plan.get("gameplans") or []

    lines.append(f"OK    engine={engine} h1_bias={bias}")
    lines.append(f"OK    zones: {len(demand)} demand, {len(supply)} supply (ranges)")

    bad_zones = [
        z for z in demand + supply
        if z.get("low") is None or z.get("high") is None
    ]
    if bad_zones:
        lines.append(f"WARN  {len(bad_zones)} zone(s) missing low/high range")
    else:
        lines.append("OK    all zones have low-high ranges")

    n = len(gps)
    want = config.intraday_gameplans
    if 4 <= n <= 5:
        lines.append(f"OK    {n} game plan line(s) (Orient FX target 4–5)")
        for i, gp in enumerate(gps[:5], 1):
            text = str(gp.get("text") or "")[:72]
            lines.append(f"      {i}. {text}{'…' if len(str(gp.get('text') or '')) > 72 else ''}")
    elif n > 0:
        lines.append(f"WARN  {n} game plan(s) — expected 4–5 (INTRADAY_GAMEPLANS={want})")
    else:
        lines.append("FAIL  no game plans in output")

    msg = plan.get("member_message") or ""
    if "Gameplan:" in msg and "Demand zones" in msg and "Supply zones" in msg:
        lines.append("OK    Telegram template: zones + numbered Gameplan block present")
    else:
        lines.append("WARN  member_message missing expected Orient FX sections")
    return lines


def _check_db_today(today: str) -> list[str]:
    lines: list[str] = []
    sql = """
        SELECT analysis_time_sgt, bias, chart_path, engine_hint
        FROM (
            SELECT analysis_time_sgt, bias, chart_path,
                   JSON_UNQUOTE(JSON_EXTRACT(gpt_output_json, '$.engine')) AS engine_hint
            FROM intraday_analyses
            WHERE DATE(analysis_time_sgt) = %s
            ORDER BY analysis_time_sgt DESC
            LIMIT 1
        ) t
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (today,))
                row = cur.fetchone()
        if row:
            chart = "yes" if row.get("chart_path") else "no"
            lines.append(
                f"OK    DB row today: {row['analysis_time_sgt']} bias={row['bias']} "
                f"chart={chart} engine={row.get('engine_hint') or '?'}"
            )
        else:
            lines.append(f"OK    no intraday_analyses row yet for {today}")
    except Exception as exc:
        lines.append(f"WARN  DB intraday check: {exc}")
    return lines


def _test_send(snapshot: dict, plan: dict) -> None:
    chart_path = charts.render(snapshot)
    header = f"🧪 TEST INTRADAY AUDIT\n{'—' * 28}\n\n"
    body = plan["member_message"]
    if chart_path:
        telegram_client.send_photo(chart_path, caption=header + body)
        print(f"  Sent TEST intraday + chart ({chart_path})")
    else:
        telegram_client.send_message(header + body + "\n\n(Chart image unavailable this session.)")
        print("  Sent TEST intraday (no chart)")


def main() -> None:
    p = argparse.ArgumentParser(description="Intraday pre-flight audit")
    p.add_argument("--fix", action="store_true", help="Remove intraday marker if set before 14:30 SGT")
    p.add_argument("--test-send", action="store_true", help="Send TEST intraday + chart to Telegram")
    args = p.parse_args()

    now = _sgt_now()
    today = now.strftime("%Y-%m-%d")
    target = _target_today(now)

    print("=" * 60)
    print("WGC XAUUSD — INTRADAY PRE-FLIGHT")
    print(f"SGT now: {now.strftime('%A, %d %b %Y %I:%M %p')}")
    print(f"Scheduled: {TARGET} SGT (window {WINDOW_MIN} min)")
    print(f"Publish: {config.publish_target} -> {config.target_chat_id}")
    print("=" * 60)

    if args.fix:
        path = _marker_path(today)
        if os.path.exists(path) and now < target:
            os.remove(path)
            print(f"\n--fix: removed premature marker {path}")
        elif os.path.exists(path):
            print(f"\n--fix: marker kept (already past {TARGET} or legit run today)")
        else:
            print("\n--fix: no intraday marker to remove")

    warnings = fails = 0
    snapshot = None
    plan = None

    sections: list[tuple[str, list[str]]] = [
        ("Config", _check_config()),
        ("Schedule (2:30 PM SGT)", _check_schedule(now, today)),
    ]

    # Live snapshot (needs FMP)
    snap_lines: list[str] = []
    upcoming_raw: list[dict] = []
    try:
        try:
            events = [e for e in calendar_service.get_outlook(now) if e.scheduled_sgt > now]
            upcoming_raw = [{
                "impact": e.impact, "event_name": e.event_name,
                "time_sgt": e.template_dict()["time_sgt"], "is_major": e.is_major,
            } for e in events]
        except Exception:
            snap_lines.append("WARN  could not load upcoming news — continuing without news risk")

        snapshot = market_data.build_snapshot(upcoming_usd_news=upcoming_raw)
        snap_lines.extend(_check_snapshot(snapshot))
    except Exception as exc:
        snap_lines.append(f"FAIL  build_snapshot: {exc}")
    sections.append(("Market snapshot (2 Asian sessions)", snap_lines))

    if snapshot:
        sections.append(("Chart payload", _check_chart_payload(snapshot)))

        plan_lines: list[str] = []
        try:
            plan = intraday.build_plan(snapshot, upcoming_raw)
            plan_lines.extend(_check_plan(plan))
        except Exception as exc:
            plan_lines.append(f"FAIL  build_plan: {exc}")
        sections.append(("Game plan (Orient FX)", plan_lines))

    sections.append(("Database (today)", _check_db_today(today)))

    for title, lines in sections:
        print(f"\n[{title}]")
        for line in lines:
            print(f"  {line}")
            if line.startswith("WARN") or line.startswith("SKIP"):
                warnings += 1
            if line.startswith("FAIL"):
                fails += 1

    print("\n" + "=" * 60)
    if fails:
        print("STATUS: NOT READY — fix FAIL items above")
    elif warnings:
        print("STATUS: MOSTLY READY — review WARN items")
    else:
        print("STATUS: GOOD TO GO for 2:30 PM SGT intraday")
    print("=" * 60)

    print("\nVerify on Telegram after 14:30 SGT:")
    print("  • Chart shows TWO Asian session boxes (08:00–16:00 SGT) on M5")
    print("  • Message: H1 bias, demand/supply zones as ranges, 4–5 numbered game plans")

    if args.test_send:
        if not snapshot or not plan:
            print("\n--test-send aborted: snapshot or plan failed")
            sys.exit(1)
        probs = config.validate()
        if probs:
            print("\n--test-send aborted:", "; ".join(probs))
            sys.exit(1)
        print("\n--test-send: publishing to Telegram…")
        _test_send(snapshot, plan)

    if warnings and not args.fix:
        print("\nTip: .venv/bin/python jobs/preflight_intraday.py --fix")


if __name__ == "__main__":
    main()
