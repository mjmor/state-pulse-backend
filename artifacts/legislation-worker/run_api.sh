#!/usr/bin/env bash
# Starts the FastAPI legislation REST server.
# Waits for MongoDB to be ready before launching uvicorn.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="${SCRIPT_DIR}/src"
export PYTHONPATH

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8001}"

# ── Wait for MongoDB ──────────────────────────────────────────────────────────
echo "[run_api] Waiting for MongoDB on localhost:27017..."
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
        echo "[run_api] MongoDB ready (${i}s)"
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "[run_api] ERROR: MongoDB not available after 30s" >&2
        exit 1
    fi
done

# ── Launch FastAPI ────────────────────────────────────────────────────────────
echo "[run_api] Starting uvicorn on ${API_HOST}:${API_PORT}..."
exec python3 -m uvicorn legislation_worker.api:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --reload \
    --reload-dir "${SCRIPT_DIR}/src"
