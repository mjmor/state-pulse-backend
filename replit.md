# Workspace

## Overview

pnpm workspace monorepo using TypeScript, plus a standalone Python legislation worker for OpenStates ETL.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database (TS)**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)
- **Python version**: 3.11
- **Task queue**: Celery 5 with Redis broker
- **Legislation DB**: MongoDB 7 (local, data at `/home/runner/workspace/data/mongodb/`)
- **Broker/cache**: Redis 7 (local, data at `/home/runner/workspace/data/redis/`)

## Structure

```text
artifacts-monorepo/
├── artifacts/              # Deployable applications
│   ├── api-server/         # Express API server
│   └── legislation-worker/ # Python Celery ETL worker (OpenStates → MongoDB)
│       ├── pyproject.toml
│       ├── run_mongodb.sh  # Starts local mongod
│       ├── run_redis.sh    # Starts local redis-server
│       ├── run_worker.sh   # Starts Celery Beat + Worker
│       └── src/legislation_worker/
│           ├── config.py        # Env-based configuration
│           ├── openstates.py    # OpenStates API v3 client (paginated)
│           ├── db.py            # MongoDB connection + bill→Legislation mapping
│           ├── celery_app.py    # Celery application factory
│           ├── celeryconfig.py  # Beat schedule (daily at midnight UTC)
│           └── tasks.py        # sync_legislation Celery task
├── data/                   # Persistent local service data (gitignored)
│   ├── mongodb/            # MongoDB data files
│   └── redis/              # Redis AOF/RDB files
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts (single workspace package)
│   └── src/                # Individual .ts scripts, run via `pnpm --filter @workspace/scripts run <script>`
├── pnpm-workspace.yaml     # pnpm workspace (artifacts/*, lib/*, lib/integrations/*, scripts)
├── tsconfig.base.json      # Shared TS options (composite, bundler resolution, es2022)
├── tsconfig.json           # Root TS project references
└── package.json            # Root package with hoisted devDeps
```

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references. This means:

- **Always typecheck from the root** — run `pnpm run typecheck` (which runs `tsc --build --emitDeclarationOnly`). This builds the full dependency graph so that cross-package imports resolve correctly. Running `tsc` inside a single package will fail if its dependencies haven't been built yet.
- **`emitDeclarationOnly`** — we only emit `.d.ts` files during typecheck; actual JS bundling is handled by esbuild/tsx/vite...etc, not `tsc`.
- **Project references** — when package A depends on package B, A's `tsconfig.json` must list B in its `references` array. `tsc --build` uses this to determine build order and skip up-to-date packages.

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages that define it
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Packages

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/` and use `@workspace/api-zod` for request and response validation and `@workspace/db` for persistence.

- Entry: `src/index.ts` — reads `PORT`, starts Express
- App setup: `src/app.ts` — mounts CORS, JSON/urlencoded parsing, routes at `/api`
- Routes: `src/routes/index.ts` mounts sub-routers; `src/routes/health.ts` exposes `GET /health` (full path: `/api/health`)
- Depends on: `@workspace/db`, `@workspace/api-zod`
- `pnpm --filter @workspace/api-server run dev` — run the dev server
- `pnpm --filter @workspace/api-server run build` — production esbuild bundle (`dist/index.cjs`)
- Build bundles an allowlist of deps (express, cors, pg, drizzle-orm, zod, etc.) and externalizes the rest

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL. Exports a Drizzle client instance and schema models.

- `src/index.ts` — creates a `Pool` + Drizzle instance, exports schema
- `src/schema/index.ts` — barrel re-export of all models
- `src/schema/<modelname>.ts` — table definitions with `drizzle-zod` insert schemas (no models definitions exist right now)
- `drizzle.config.ts` — Drizzle Kit config (requires `DATABASE_URL`, automatically provided by Replit)
- Exports: `.` (pool, db, schema), `./schema` (schema only)

Production migrations are handled by Replit when publishing. In development, we just use `pnpm --filter @workspace/db run push`, and we fallback to `pnpm --filter @workspace/db run push-force`.

### `lib/api-spec` (`@workspace/api-spec`)

Owns the OpenAPI 3.1 spec (`openapi.yaml`) and the Orval config (`orval.config.ts`). Running codegen produces output into two sibling packages:

1. `lib/api-client-react/src/generated/` — React Query hooks + fetch client
2. `lib/api-zod/src/generated/` — Zod schemas

Run codegen: `pnpm --filter @workspace/api-spec run codegen`

### `lib/api-zod` (`@workspace/api-zod`)

Generated Zod schemas from the OpenAPI spec (e.g. `HealthCheckResponse`). Used by `api-server` for response validation.

### `lib/api-client-react` (`@workspace/api-client-react`)

Generated React Query hooks and fetch client from the OpenAPI spec (e.g. `useHealthCheck`, `healthCheck`).

### `scripts` (`@workspace/scripts`)

Utility scripts package. Each script is a `.ts` file in `src/` with a corresponding npm script in `package.json`. Run scripts via `pnpm --filter @workspace/scripts run <script>`. Scripts can import any workspace package (e.g., `@workspace/db`) by adding it as a dependency in `scripts/package.json`.

---

## Legislation Worker (Python)

A standalone Python project at `artifacts/legislation-worker/` that runs a Celery-based ETL pipeline.

### How it works

1. **Celery Beat** fires the `sync_legislation` task once every 24 hours (daily at midnight UTC).
2. **Celery Worker** executes the task: computes `updated_since = now - 24h`, calls the OpenStates API `/bills` endpoint with `updated_since` and full `include` params, paginates through all results per jurisdiction, and upserts each bill into MongoDB.
3. **MongoDB** stores the bills in the `legislation` collection with the schema from the state-pulse `Legislation` interface.

### Workflows

| Workflow | Command | Purpose |
|---|---|---|
| `Start MongoDB` | `run_mongodb.sh` | Starts `mongod` on `localhost:27017`, data in `data/mongodb/` |
| `Start Redis` | `run_redis.sh` | Starts `redis-server` on `localhost:6379`, data in `data/redis/` |
| `Start Celery` | `run_worker.sh` | Starts Celery Beat + Worker; health-checks MongoDB (pymongo ping) and Redis (`redis-cli ping`) before proceeding |

| `Legislation API` | `run_api.sh` | FastAPI REST server on port 8001; health-checks MongoDB before starting |

**Start order**: `Start MongoDB` → `Start Redis` → `Start Celery`, `Legislation API` (both depend on MongoDB)

### REST API Endpoints (port 8001)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | MongoDB health check |
| `GET` | `/api/jurisdictions` | List all jurisdictions present in the DB |
| `GET` | `/api/legislation` | Paginated bill list (filters: `jurisdiction`, `session`, `classification`, `q`, `updated_since`, `page`, `limit`) |
| `GET` | `/api/legislation/{id}` | Single bill by OpenStates ID |
| `GET` | `/docs` | Auto-generated Swagger UI |

**External base URL** (stored in `LEGISLATION_API_URL` env var):
```
https://0c2e72a2-c56c-4994-b869-80633822760a-00-11mwnqkdda7si.riker.replit.dev:8001
```

### Configuration (env vars / secrets)

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENSTATES_API_KEY` | Yes (secret) | — | OpenStates API key |
| `MONGODB_URI` | No | `mongodb://localhost:27017` | MongoDB connection URI |
| `MONGODB_DB` | No | `state_pulse` | Database name |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis broker URL |
| `JURISDICTIONS` | No | All 50 states + DC | Comma-separated state codes, e.g. `CA,NY,TX` |
| `SYNC_LOOKBACK_HOURS` | No | `24` | Hours to look back for updated bills |

### Python source layout

```
src/legislation_worker/
├── config.py        # Reads env vars, defines JURISDICTIONS list
├── openstates.py    # Paginated OpenStates /bills client with retry logic
├── db.py            # pymongo connection, index creation, bill→Legislation mapping
├── celery_app.py    # Celery app factory
├── celeryconfig.py  # Beat schedule (crontab 0 0 * * *)
└── tasks.py         # sync_legislation task
```
