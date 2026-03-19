#!/usr/bin/env bash
# Starts the local Redis instance with a persistent data directory.

set -euo pipefail

DATA_DIR="${REDIS_DATA_DIR:-/home/runner/workspace/data/redis}"
mkdir -p "$DATA_DIR"

echo "[run_redis] Starting redis-server — data dir: $DATA_DIR"
exec redis-server \
    --bind 127.0.0.1 \
    --port 6379 \
    --dir "$DATA_DIR" \
    --appendonly yes \
    --loglevel notice
