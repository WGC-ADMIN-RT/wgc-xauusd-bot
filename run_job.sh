#!/usr/bin/env bash
# Cron wrapper — run from cPanel with the job script as the first argument.
# Uses flock so overlapping cron ticks don't double-run the same job.
#
# Example (cPanel Cron, every 5 minutes — outlook self-gates to 12:00 SGT once/day):
#   /bin/bash /home/YOUR_USER/wgc-xauusd-bot/run_job.sh jobs/run_outlook.py
#
# Example (every minute — news alerts + post-release):
#   /bin/bash /home/YOUR_USER/wgc-xauusd-bot/run_job.sh jobs/run_news_cycle.py
set -u
APP="${HOME:?HOME not set}/wgc-xauusd-bot"
JOB="${1:?usage: run_job.sh jobs/<script>.py [--force]}"
shift || true
LOCK="${TMPDIR:-/tmp}/wgc-$(basename "$JOB" .py).lock"
cd "$APP" || exit 1
exec flock -n "$LOCK" "$APP/.venv/bin/python" "$JOB" "$@"
