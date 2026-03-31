# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All Python code lives in `artifacts/legislation-worker/`. Run commands from that directory unless noted.

```bash
cd artifacts/legislation-worker

# Install dependencies
uv sync

# Start the FastAPI server (port 8001)
uv run uvicorn legislation_worker.api:app --host 0.0.0.0 --port 8001

# Start the Celery worker
uv run celery -A legislation_worker.celery_app worker --loglevel=info

# Start the Celery Beat scheduler (daily sync at UTC 0:00)
uv run celery -A legislation_worker.celery_app beat --loglevel=info

# Manually trigger a Celery task
uv run python -c "from legislation_worker.tasks import sync_legislation; sync_legislation.delay()"

# Create an API key (run from artifacts/legislation-worker/)
uv run python -c "
from legislation_worker.auth import create_key_in_db
key = create_key_in_db('my-client', scopes=['read'])
print(key)
"
```

The repo also has a root `pyproject.toml` so `uv run` works from the repo root.

There is no test infrastructure configured yet.

## Architecture

This service syncs US state legislation from the OpenStates API, stores bills in MongoDB, vectorizes them into PostgreSQL (pgvector) for semantic search, and exposes a FastAPI REST API.

### Data Flow

```
OpenStates API → [Celery: sync_legislation]  → MongoDB (raw bills)
MongoDB        → [Celery: fetch_bill_texts]   → MongoDB (+ fullText field)
MongoDB        → [Celery: vectorize_bills]    → PostgreSQL bill_chunks (pgvector)
MongoDB + pgvector → FastAPI (port 8001)      → clients
```

The three Celery tasks form a sequential ETL pipeline, scheduled daily at UTC 0:00 via Celery Beat.

### Key Files (`artifacts/legislation-worker/src/legislation_worker/`)

| File | Purpose |
|------|---------|
| `api.py` | FastAPI app — all REST endpoints |
| `auth.py` | API key auth (SHA-256 hash lookup in PostgreSQL `api_keys` table) |
| `tasks.py` | Celery tasks: `sync_legislation`, `fetch_bill_texts`, `vectorize_bills` |
| `openstates.py` | OpenStates API v3 client with pagination and retry |
| `db.py` | MongoDB connection and bill upsert logic |
| `text_fetcher.py` | Extracts plaintext from state legislature HTML pages |
| `chunker.py` | LangChain-based bill text chunking |
| `vectorizer.py` | HuggingFace Sentence Transformers embedding (`all-MiniLM-L6-v2`) |
| `vector_store.py` | pgvector schema (`bill_chunks` table) and semantic search |
| `config.py` | Environment variable loading |
| `celery_app.py` | Celery app factory |
| `celeryconfig.py` | Beat schedule (daily crontab) and broker config |

### Databases

- **MongoDB** (`state_pulse.legislation`) — Raw bill documents from OpenStates. Managed by `db.py`.
- **PostgreSQL + pgvector** (`bill_chunks` table) — Chunked bill embeddings. Schema created by `vector_store.py` on startup; not managed by any ORM.
- **PostgreSQL** (`api_keys` table) — API key storage. Created by `vector_store.ensure_schema()`.
- **Redis** — Celery broker and result backend.

### API Authentication

All endpoints except `GET /health` require an `X-API-Key` header. Keys are stored as SHA-256 hashes in the PostgreSQL `api_keys` table. The `admin` scope is required for `POST /api/legislation/vectorize`. Default scope is `read`.

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENSTATES_API_KEY` | *(required)* | OpenStates API v3 key |
| `DATABASE_URL` | *(required)* | PostgreSQL connection string (pgvector + API keys) |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB URI |
| `MONGODB_DB` | `state_pulse` | MongoDB database name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker URL |
| `JURISDICTIONS` | all 50 states + DC | Comma-separated state codes to sync (e.g. `CA,NY,TX`) |
| `SYNC_LOOKBACK` | `24` | Hours to look back for updates, or `"all"` for full sync |
| `SUBJECT_FILTER` | *(none)* | Optional OpenStates subject filter |
| `API_HOST` | `0.0.0.0` | FastAPI bind host |
| `API_PORT` | `8001` | FastAPI bind port |
| `ROOT_PATH` | `/legislation-api` | FastAPI root path |
