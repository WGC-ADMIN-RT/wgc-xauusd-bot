# WGC XAUUSD Automation Bot

Telegram automation for the Wings Gold Club — USD news automation and daily XAU/USD
intraday analysis. **Phase 1** (this build): news + analysis only. No trade signals yet
(those are Phase 2, with manual admin approval).

## Scope (Phase 1)

**Task 1 — USD News Automation**
- Daily USD news outlook at **12:00 PM SGT** (high + medium impact, 24h forward window).
- Pre-news alerts **1 hour** and **15 minutes** before each event.
- Post-news breakdown: Actual vs Forecast, Previous vs Actual, USD impact, XAUUSD impact,
  and a deterministic **bullish / bearish / neutral** read (news polarity rules).

**Task 2 — Daily Intraday Analysis at 2:30 PM SGT**
- Market bias, key support/resistance, buy & sell scenarios, invalidation, news risk.
- Chart attached (self-rendered by default; TradingView/Chart-IMG optional via config).

All user-facing times are **SGT (Asia/Singapore)**; all timestamps stored **UTC**.

## Architecture

| Module | File | Responsibility |
|---|---|---|
| Config | `src/config.py` | Loads settings/secrets from environment. |
| Database | `src/db.py` + `schema.sql` | MySQL: `economic_events`, `intraday_analyses`. |
| News polarity | `src/polarity.py` | Deterministic event → USD/XAUUSD bias (no AI). |
| Calendar service | `src/calendar_service.py` | Fetches USD news (FMP) — forecast/previous/actual. |
| Market data | `src/market_data.py` | XAU/USD candles, price, EMAs, ATR, session levels. |
| Chart renderer | `src/charts.py` | 200-candle M15 chart image. |
| Intraday analysis | `src/intraday.py` | Builds the daily plan (rule-based). |
| Templates | `src/templates.py` | Message formatting (SGT). |
| Publisher | `src/telegram_client.py` | Sends messages/photos to Telegram. |
| Jobs | `jobs/` | Cron entry points (outlook, alerts, post-news, intraday). |

## Deploy target

OrangeHost cPanel (`upayztec`), Python 3.9 shared venv at
`/home/upayztec/virtualenv/monitor/3.9/bin/python`. Runs via cron + flock, isolated from
the existing `monitor` and `wings-gold-bot` jobs.

## Secrets

Never committed. Live as environment variables on the server (see `.env.example`).
