#!/usr/bin/env bash
# Starts both Celery Beat and Celery Worker in the same process group.
# Beat handles scheduling; worker executes the tasks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="${SCRIPT_DIR}/src"
export PYTHONPATH

cd "$SCRIPT_DIR"

echo "[run_worker] Starting Celery Beat..."
python3 -m celery -A legislation_worker.celery_app beat \
    --loglevel=info &
BEAT_PID=$!

echo "[run_worker] Starting Celery Worker..."
python3 -m celery -A legislation_worker.celery_app worker \
    --loglevel=info \
    --concurrency=2 &
WORKER_PID=$!

trap "kill $BEAT_PID $WORKER_PID 2>/dev/null; exit 0" SIGTERM SIGINT

echo "[run_worker] Beat PID=$BEAT_PID  Worker PID=$WORKER_PID"
wait $BEAT_PID $WORKER_PID
