#!/usr/bin/env bash
# Runs the tagger web app in the background with nohup. Application logging
# (requests + tag/source/scan actions, via the stdlib logging module) goes
# to logs/app.log; uvicorn's own startup/crash output goes to logs/app.out.
# Stop it with `kill $(cat logs/app.pid)`.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

mkdir -p logs

nohup uv run uvicorn tagger.main:app --host 127.0.0.1 --port 3051 --no-access-log >> logs/app.out 2>&1 &
echo $! > logs/app.pid

echo "tagger started (pid $(cat logs/app.pid)) at http://127.0.0.1:3051"
echo "app log: logs/app.log — startup/crash output: logs/app.out"
