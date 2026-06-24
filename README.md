# WGC XAUUSD Automation Bot

Telegram automation for the Wings Gold Club — USD news automation and daily XAU/USD
intraday analysis. **Phase 1** (this build): news + analysis only. No trade signals yet
(those are Phase 2, with manual admin approval).

## Scope (Phase 1)

**Task 1 — USD News Automation**
- Daily USD news outlook at **12:00 PM SGT** (Forex Factory USD red/orange events relevant to XAUUSD, 24h forward window).
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
| Calendar service | `src/calendar_service.py` + `src/forex_factory.py` | USD news from Forex Factory (red/orange); FMP for actual fallback. |
| Market data | `src/market_data.py` | XAU/USD candles, price, EMAs, ATR, session levels. |
| Chart renderer | `src/charts.py` | 200-candle intraday chart image (M5 default, `INTRADAY_TF`). |
| Intraday analysis | `src/intraday.py` | Builds the daily plan (rule-based). |
| Templates | `src/templates.py` | Message formatting (SGT). |
| Publisher | `src/telegram_client.py` | Sends messages/photos to Telegram. |
| Jobs | `jobs/` | Cron entry points (outlook, alerts, post-news, intraday). |

## Deploy target

OrangeHost **cPanel** (any account). The app lives at `~/wgc-xauusd-bot` with its own
`.venv`. Cron entries use `run_job.sh` + `flock` so overlapping ticks never double-send
(see `cron.example`).

## First-time setup (new cPanel account)

1. **MySQL** — cPanel → *MySQL Databases*: create database `YOUR_USER_wgcxau`, user, and
   password; grant the user *All Privileges* on that database.
2. **Code** — cPanel → *Git Version Control* (clone
   `https://github.com/WGC-ADMIN-RT/wgc-xauusd-bot.git`) **or** upload the folder to
   `~/wgc-xauusd-bot`.
3. **Secrets** — copy `.env.example` → `.env`, fill in DB creds + API keys (same Telegram
   / FMP keys as before if you are migrating).
4. **Install** — cPanel → *Terminal*: `cd ~/wgc-xauusd-bot && bash deploy.sh`
5. **Cron** — cPanel → *Cron Jobs*: add the three lines from `cron.example` (replace
   `YOUR_USER` with your cPanel username).
6. **Smoke test** — in Terminal (safe any time of day; `--force` does **not** block the
   scheduled 12:00 / 14:30 SGT sends):
   `.venv/bin/python jobs/run_outlook.py --force`
   `.venv/bin/python jobs/run_intraday.py --force`
   Confirm messages arrive in the WGC Bots Telegram group.

## Migrating from another OrangeHost account (e.g. upayztec → new account)

1. On the **new** account: complete *First-time setup* steps 1–5 above.
2. Copy the **old** `.env` and change only the three DB lines (`DB_NAME`, `DB_USER`,
   `DB_PASSWORD`) to the new database. Keep Telegram, FMP, Chart-IMG, and Anthropic keys
   unchanged unless you are rotating them.
3. Run the smoke-test commands on the new account.
4. On the **old** account: disable or delete the three WGC cron jobs so nothing posts twice.

The old account’s MySQL data is **not** copied automatically; Phase 1 only needs empty
tables (created by `deploy.sh`). Alert flags (`sent_*`) start fresh on the new DB.

## Secrets

Never committed. Live in `~/wgc-xauusd-bot/.env` on the server (see `.env.example`).
