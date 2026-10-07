#!/usr/bin/env bash
# Start Szofie. Creates the virtualenv and installs dependencies on first run.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.lock
fi

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill it in first:"
  echo "    cp .env.example .env"
  exit 1
fi

exec .venv/bin/python bot.py
