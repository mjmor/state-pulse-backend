#!/usr/bin/env bash
# Starts Celery Beat + Worker.
# Waits for MongoDB and Redis to be healthy before proceeding.
# Run AFTER Start MongoDB and Start Redis workflows are up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="${SCRIPT_DIR}/src"
export PYTHONPATH

# ── Wait for MongoDB ──────────────────────────────────────────────────────────
echo "[run_worker] Waiting for MongoDB on localhost:27017..."
for i in $(seq 1 30); do
    if python3 -c "
import pymongo, sys
try:
    c = pymongo.MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=800)
    c.admin.command('ping')
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        echo "[run_worker] MongoDB ready (${i}s)"
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "[run_worker] ERROR: MongoDB not available after 30s" >&2
        exit 1
    fi
done

# ── Wait for Redis ────────────────────────────────────────────────────────────
echo "[run_worker] Waiting for Redis on localhost:6379..."
for i in $(seq 1 15); do
    if redis-cli -h 127.0.0.1 ping 2>/dev/null | grep -q PONG; then
        echo "[run_worker] Redis ready (${i}s)"
        break
    fi
    sleep 1
    if [ "$i" -eq 15 ]; then
        echo "[run_worker] ERROR: Redis not available after 15s" >&2
        exit 1
    fi
done

# ── Celery Beat + Worker ──────────────────────────────────────────────────────
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

echo "[run_worker] Beat PID=$BEAT_PID  Worker PID=$WORKER_PID"

trap "echo '[run_worker] Shutting down...'; kill $BEAT_PID $WORKER_PID 2>/dev/null; exit 0" SIGTERM SIGINT

wait $BEAT_PID $WORKER_PID
