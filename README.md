# state-pulse-backend

Ingests US state legislation from the [OpenStates API](https://openstates.org/api/), stores bills in MongoDB, vectorizes them into PostgreSQL (pgvector) for semantic search, and exposes a FastAPI REST API.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- MongoDB 7+ (macOS: `brew tap mongodb/brew && brew install mongodb-community`)
- PostgreSQL 16+ with the `pgvector` extension enabled (macOS: `brew install postgresql@18`)
- Redis 7+ (macOS: `brew install redis`)

## Setup

### 1. Start services

On macOS (Homebrew):

```bash
brew services start mongodb/brew/mongodb-community
brew services start postgresql@18   # or whichever version you installed
brew services start redis
```

> **Note:** Homebrew installs PostgreSQL binaries under `/opt/homebrew/opt/postgresql@<version>/bin/`. Add that directory to your `PATH`, or use full paths for `psql`, `createdb`, etc.

### 2. Create the PostgreSQL database

```bash
createdb state_pulse
```

### 3. Install Python dependencies

```bash
cd artifacts/legislation-worker
uv sync
```

### 4. Configure environment variables

Create a `.env` file in `artifacts/legislation-worker/` (or export variables in your shell):

```env
OPENSTATES_API_KEY=your_key_here
DATABASE_URL=postgresql://localhost/state_pulse
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=state_pulse
REDIS_URL=redis://localhost:6379/0
```

> On a local Homebrew PostgreSQL install there is no password by default — use `postgresql://localhost/state_pulse` (peer/trust auth). Add credentials only if your server requires them.

The `api_keys` and `bill_chunks` tables are created automatically when the API server starts (the `pgvector` extension is also enabled automatically).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENSTATES_API_KEY` | *(required)* | OpenStates API v3 authentication key |
| `DATABASE_URL` | *(required)* | PostgreSQL connection string |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection URI |
| `MONGODB_DB` | `state_pulse` | MongoDB database name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker URL for Celery |
| `JURISDICTIONS` | all 50 states + DC | Comma-separated state codes to sync (e.g. `CA,NY,TX`) |
| `SYNC_LOOKBACK` | `24` | Hours to look back for bill updates; use `"all"` for a full historical sync |
| `SUBJECT_FILTER` | *(none)* | Optional OpenStates subject filter (e.g. `energy`) |
| `API_HOST` | `0.0.0.0` | FastAPI bind host |
| `API_PORT` | `8001` | FastAPI bind port |
| `ROOT_PATH` | `/legislation-api` | FastAPI root path prefix |

## Running the Services

Run directly with uv from `artifacts/legislation-worker/`:

```bash
# FastAPI server
uv run uvicorn legislation_worker.api:app --host 0.0.0.0 --port 8001

# Celery worker
uv run celery -A legislation_worker.celery_app worker --loglevel=info

# Celery beat (scheduler — runs tasks daily at UTC 0:00)
uv run celery -A legislation_worker.celery_app beat --loglevel=info
```

## Creating an API Key

All API endpoints (except `/health`) require an `X-API-Key` header. Generate a key once and store it — it is only displayed once.

```bash
cd artifacts/legislation-worker
uv run python -c "
from legislation_worker.auth import create_key_in_db
key = create_key_in_db('my-client', description='Local dev', scopes=['read'])
print(key)
"
```

For admin operations (e.g. triggering vectorization), include `'admin'` in the scopes list:

```bash
uv run python -c "
from legislation_worker.auth import create_key_in_db
key = create_key_in_db('admin-client', scopes=['read', 'admin'])
print(key)
"
```

## API Usage

Base URL: `http://localhost:8001/legislation-api` (or your deployed URL)

Interactive docs: `http://localhost:8001/legislation-api/docs`

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | MongoDB health check |
| `GET` | `/api/jurisdictions` | API key | List all synced states |
| `GET` | `/api/legislation` | API key | Paginated bill list with filters |
| `GET` | `/api/legislation/search` | API key | Semantic similarity search |
| `GET` | `/api/legislation/{bill_id}` | API key | Single bill by OpenStates ID |
| `POST` | `/api/legislation/fetch-texts` | API key | Queue full-text fetch task |
| `POST` | `/api/legislation/vectorize` | API key (`admin`) | Queue vectorization task |

### Example Requests

```bash
API_KEY=your_api_key_here
BASE=http://localhost:8001/legislation-api

# Health check
curl $BASE/health

# List legislation for Michigan
curl -H "X-API-Key: $API_KEY" "$BASE/api/legislation?jurisdiction=MI&limit=10"

# Full-text search by title
curl -H "X-API-Key: $API_KEY" "$BASE/api/legislation?q=renewable+energy"

# Semantic search
curl -H "X-API-Key: $API_KEY" "$BASE/api/legislation/search?q=solar+panel+subsidies&jurisdiction=CA"

# Get a specific bill
curl -H "X-API-Key: $API_KEY" "$BASE/api/legislation/ocd-bill/..."
```

### Filtering `GET /api/legislation`

| Parameter | Description |
|-----------|-------------|
| `jurisdiction` | 2-letter state code (e.g. `MI`) or full OCD ID |
| `session` | Legislative session string |
| `classification` | Bill type: `bill`, `resolution`, etc. |
| `subject` | Policy area (e.g. `energy`, `health`) |
| `q` | Case-insensitive title search |
| `updated_since` | ISO 8601 datetime — only bills updated after this |
| `page` | Page number (default: 1) |
| `limit` | Results per page (default: 20, max: 100) |

## Manually Triggering ETL Tasks

The ETL pipeline runs automatically via Celery Beat (daily at UTC 0:00), but you can trigger tasks manually:

```bash
cd artifacts/legislation-worker

# Step 1: Sync bills from OpenStates → MongoDB
uv run python -c "from legislation_worker.tasks import sync_legislation; sync_legislation.delay()"

# Step 2: Fetch full bill text from state legislature websites
uv run python -c "from legislation_worker.tasks import fetch_bill_texts; fetch_bill_texts.delay()"

# Step 3: Vectorize bills into PostgreSQL (pgvector)
uv run python -c "from legislation_worker.tasks import vectorize_bills; vectorize_bills.delay()"
```

For a full historical sync (instead of the default 24-hour lookback):

```bash
SYNC_LOOKBACK=all uv run python -c "from legislation_worker.tasks import sync_legislation; sync_legislation.delay()"
```

Or trigger via the API (steps 2 and 3):

```bash
curl -X POST -H "X-API-Key: $API_KEY" $BASE/api/legislation/fetch-texts
curl -X POST -H "X-API-Key: $API_KEY" $BASE/api/legislation/vectorize  # requires admin scope
```

## Database Backups and Restoration

### Backing up

```bash
DATE=$(date +%Y-%m-%d)

# MongoDB — export the legislation collection as gzipped JSONL
mongoexport --db state_pulse --collection legislation \
  | gzip > data/${DATE}_mongodb_legislation.jsonl.gz

# PostgreSQL — dump the full database as gzipped SQL
pg_dump state_pulse | gzip > data/${DATE}_postgres_export.sql.gz
```

### Restoring

```bash
# MongoDB — drop and reimport the legislation collection
gunzip -c data/<date>_mongodb_legislation.jsonl.gz \
  | mongoimport --db state_pulse --collection legislation --drop

# PostgreSQL — create the database if needed, then restore
createdb state_pulse 2>/dev/null || true
gunzip -c data/<date>_postgres_export.sql.gz | psql state_pulse
```

The `data/` directory is gitignored. Backup files should be stored there or in a separate offsite location.
