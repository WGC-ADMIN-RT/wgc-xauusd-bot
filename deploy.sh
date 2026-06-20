#!/usr/bin/env bash
# Server setup for the WGC XAUUSD bot (OrangeHost cPanel, no SSH — runs via a cron
# one-shot). Idempotent: safe to re-run. Assumes the repo is cloned at ~/wgc-xauusd-bot
# and a .env file is present there.
set -u
export HOME="${HOME:-/home/upayztec}"
APP="$HOME/wgc-xauusd-bot"
cd "$APP" || { echo "App dir $APP missing"; exit 1; }

echo "=== WGC XAUUSD deploy $(date) ==="

# 1) Locate a Python 3.9+ interpreter (prefer the real interpreter over a venv)
PY=""
for cand in \
  "$(command -v python3.9 || true)" \
  /opt/alt/python39/bin/python3.9 \
  /opt/alt/python311/bin/python3.11 \
  "$HOME/virtualenv/monitor/3.9/bin/python" \
  "$(command -v python3 || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
done
[ -z "$PY" ] && { echo "No python interpreter found"; exit 1; }
echo "Base python: $PY ($($PY --version 2>&1))"

# 2) Create the dedicated venv (idempotent)
if [ ! -x "$APP/.venv/bin/python" ]; then
  echo "Creating venv..."
  "$PY" -m venv "$APP/.venv" || { echo "venv creation FAILED"; exit 1; }
fi
VPY="$APP/.venv/bin/python"

# 3) Install core requirements (fast/light; charts installed separately later)
echo "Installing core requirements..."
"$VPY" -m pip install --upgrade pip -q || true
"$VPY" -m pip install -q -r requirements.txt || { echo "core pip install FAILED"; exit 1; }
echo "Core install OK: $("$VPY" -m pip --version)"

# 4) Create DB tables
echo "--- init_schema ---"
"$VPY" -c "import sys; sys.path.insert(0,'src'); import bootstrap; bootstrap.init(); import db; db.init_schema(); print('schema OK')" \
  || echo "init_schema FAILED (check DB creds in .env)"

# 5) Discover the group chat IDs (writes them to this log)
echo "--- discover_chat_ids ---"
"$VPY" jobs/discover_chat_ids.py || echo "discover_chat_ids FAILED (check TELEGRAM_BOT_TOKEN / group membership)"

echo "=== DEPLOY DONE $(date) ==="
