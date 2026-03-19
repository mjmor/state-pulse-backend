#!/usr/bin/env bash
# Starts MongoDB, Redis, then Celery Beat + Worker in the correct order.
# Each service is health-checked before the next one starts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="${SCRIPT_DIR}/src"
export PYTHONPATH

# ── MongoDB ──────────────────────────────────────────────────────────────────
DATA_DIR="${MONGODB_DATA_DIR:-/home/runner/workspace/data/mongodb}"
mkdir -p "$DATA_DIR"

echo "[run_worker] Starting mongod..."
mongod \
    --dbpath "$DATA_DIR" \
    --bind_ip 127.0.0.1 \
    --port 27017 \
    --logpath "$DATA_DIR/mongod.log" \
    --logappend &
MONGO_PID=$!

# Wait for MongoDB to accept connections (up to 30 s) using pymongo ping
echo "[run_worker] Waiting for MongoDB..."
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
        echo "[run_worker] ERROR: MongoDB did not start in time" >&2
        kill "$MONGO_PID" 2>/dev/null || true
        exit 1
    fi
done

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_DATA_DIR="${REDIS_DATA_DIR:-/home/runner/workspace/data/redis}"
mkdir -p "$REDIS_DATA_DIR"

echo "[run_worker] Starting redis-server..."
redis-server \
    --bind 127.0.0.1 \
    --port 6379 \
    --dir "$REDIS_DATA_DIR" \
    --appendonly yes \
    --loglevel notice &
REDIS_PID=$!

# Wait for Redis to be ready (up to 15 s)
echo "[run_worker] Waiting for Redis..."
for i in $(seq 1 15); do
    if redis-cli -h 127.0.0.1 ping 2>/dev/null | grep -q PONG; then
        echo "[run_worker] Redis ready (${i}s)"
        break
    fi
    sleep 1
    if [ "$i" -eq 15 ]; then
        echo "[run_worker] ERROR: Redis did not start in time" >&2
        kill "$MONGO_PID" "$REDIS_PID" 2>/dev/null || true
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

echo "[run_worker] All services running:"
echo "  mongod  PID=$MONGO_PID"
echo "  redis   PID=$REDIS_PID"
echo "  beat    PID=$BEAT_PID"
echo "  worker  PID=$WORKER_PID"

trap "echo '[run_worker] Shutting down...'; kill $BEAT_PID $WORKER_PID $REDIS_PID $MONGO_PID 2>/dev/null; exit 0" SIGTERM SIGINT

wait $BEAT_PID $WORKER_PID $REDIS_PID $MONGO_PID
