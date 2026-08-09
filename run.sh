#!/usr/bin/env bash
# Runs the tagger web app in the background with nohup, logging to
# logs/app.log. Use stop.sh (or `kill $(cat logs/app.pid)`) to stop it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

mkdir -p logs

nohup uv run uvicorn tagger.main:app --host 127.0.0.1 --port 3500 >> logs/app.log 2>&1 &
echo $! > logs/app.pid

echo "tagger started (pid $(cat logs/app.pid)) at http://127.0.0.1:3500 — logging to logs/app.log"
