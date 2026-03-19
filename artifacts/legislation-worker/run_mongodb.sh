#!/usr/bin/env bash
# Starts the local MongoDB instance with a persistent data directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${MONGODB_DATA_DIR:-/home/runner/workspace/data/mongodb}"

mkdir -p "$DATA_DIR"

echo "[run_mongodb] Starting mongod — data dir: $DATA_DIR"
exec mongod \
    --dbpath "$DATA_DIR" \
    --bind_ip 127.0.0.1 \
    --port 27017 \
    --logpath "$DATA_DIR/mongod.log" \
    --logappend
