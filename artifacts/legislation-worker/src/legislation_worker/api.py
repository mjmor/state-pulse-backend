"""FastAPI REST layer over the MongoDB legislation collection."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo.collection import Collection

from .auth import require_api_key
from .db import get_collection
from .tasks import fetch_bill_texts, vectorize_bills

_ROOT_PATH = os.environ.get("ROOT_PATH", "/legislation-api")

_OCD_PREFIX = "ocd-jurisdiction/country:us/state:"
_OCD_SUFFIX = "/government"


def _normalize_jurisdiction(value: str) -> str:
    """Accept a 2-letter state abbreviation OR a full OCD jurisdiction ID.

    Examples:
      "MI"  → "ocd-jurisdiction/country:us/state:mi/government"
      "ocd-jurisdiction/country:us/state:mi/government" → unchanged
    """
    v = value.strip()
    if v.startswith("ocd-jurisdiction/"):
        return v
    if len(v) == 2 and v.isalpha():
        return f"{_OCD_PREFIX}{v.lower()}{_OCD_SUFFIX}"
    return v

app = FastAPI(
    title="Legislation API",
    description="Query US state legislation synced from OpenStates. Authenticate with `X-API-Key` header.",
    version="1.0.0",
    root_path=_ROOT_PATH,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*", "X-API-Key"],
)


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    doc.pop("_id", None)
    for key, value in doc.items():
        if isinstance(value, datetime):
            doc[key] = value.isoformat()
    return doc


def _get_col() -> Collection:
    return get_collection()


# ── Health (no auth) ──────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health() -> dict[str, str]:
    """Returns ok when MongoDB is reachable. No authentication required."""
    try:
        _get_col().database.client.admin.command("ping")
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MongoDB unavailable: {exc}")


# ── Jurisdictions ─────────────────────────────────────────────────────────────

@app.get("/api/jurisdictions", tags=["Legislation"])
def list_jurisdictions(
    _key: dict = Depends(require_api_key),
) -> list[dict[str, str]]:
    """Return all unique jurisdiction IDs and names present in the database."""
    col = _get_col()
    pipeline = [
        {"$group": {"_id": "$jurisdictionId", "name": {"$first": "$jurisdictionName"}}},
        {"$sort": {"name": 1}},
    ]
    return [{"id": r["_id"], "name": r["name"]} for r in col.aggregate(pipeline) if r["_id"]]


# ── Legislation list ──────────────────────────────────────────────────────────

@app.get("/api/legislation", tags=["Legislation"])
def list_legislation(
    jurisdiction: str | None = Query(None, description="Jurisdiction ID or state abbreviation"),
    session: str | None = Query(None, description="Legislative session string"),
    classification: str | None = Query(None, description="Bill classification (e.g. 'bill', 'resolution')"),
    subject: str | None = Query(None, description="Policy area / subject (e.g. 'energy', 'health')"),
    q: str | None = Query(None, description="Full-text search on title (case-insensitive)"),
    updated_since: str | None = Query(None, description="ISO 8601 datetime — only bills updated after this"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """List legislation with optional filters and pagination."""
    col = _get_col()

    filt: dict[str, Any] = {}
    if jurisdiction:
        filt["jurisdictionId"] = _normalize_jurisdiction(jurisdiction)
    if session:
        filt["session"] = session
    if classification:
        filt["classification"] = classification
    if subject:
        filt["subjects"] = {"$in": [subject]}
    if q:
        filt["title"] = {"$regex": q, "$options": "i"}
    if updated_since:
        try:
            dt = datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid updated_since — use ISO 8601")
        filt["updatedAt"] = {"$gte": dt}

    total = col.count_documents(filt)
    skip = (page - 1) * limit

    docs = list(
        col.find(filt, {"_id": 0})
           .sort("latestActionAt", -1)
           .skip(skip)
           .limit(limit)
    )

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
        "results": [_serialize(d) for d in docs],
    }


# ── Semantic search ───────────────────────────────────────────────────────────
# NOTE: This GET route MUST be declared before /{bill_id:path} to avoid being
# swallowed by the path wildcard.

@app.get("/api/legislation/search", tags=["Search"])
def semantic_search_endpoint(
    q: str = Query(..., description="Natural language search query"),
    jurisdiction: str | None = Query(None, description="State abbreviation or full OCD jurisdiction ID"),
    classification: str | None = Query(None, description="Bill classification filter (e.g. 'bill', 'resolution')"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results"),
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """Semantic similarity search over vectorized bills.

    Embeds the query using ``sentence-transformers/all-MiniLM-L6-v2`` and
    performs a cosine-distance search in the ``bill_chunks`` pgvector table.
    Results are deduplicated to one row per bill (best-matching chunk) and
    ranked by descending similarity score.

    Args:
        q:              Natural language query (e.g. "solar energy mandate").
        jurisdiction:   Optional 2-letter state code or full OCD jurisdiction ID.
        classification: Optional bill type filter (substring match).
        limit:          Number of bills to return (1–50, default 10).
    """
    from .vector_store import semantic_search

    if not q.strip():
        raise HTTPException(status_code=400, detail="Query 'q' must not be empty")

    jurisdiction_id = _normalize_jurisdiction(jurisdiction) if jurisdiction else None

    try:
        results = semantic_search(
            query_text=q,
            k=limit,
            jurisdiction=jurisdiction_id,
            classification=classification,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Search unavailable: {exc}",
        )

    return {
        "query": q,
        "jurisdiction": jurisdiction,
        "total": len(results),
        "results": results,
    }


# ── Single bill ───────────────────────────────────────────────────────────────

@app.get("/api/legislation/{bill_id:path}", tags=["Legislation"])
def get_legislation(
    bill_id: str,
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """Fetch a single legislation document by its OpenStates ID."""
    col = _get_col()
    doc = col.find_one({"id": bill_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found")
    return _serialize(doc)


# ── Text fetching ──────────────────────────────────────────────────────────────

@app.post("/api/legislation/fetch-texts", tags=["Enrichment"])
def trigger_fetch_texts(
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """Queue a background task to fetch full bill text for all un-fetched bills.

    Processes every bill in MongoDB where ``fullTextFetchedAt`` is null,
    fetching the HTML text from state legislature URLs stored in ``versions``.
    Idempotent — bills with ``fullTextFetchedAt`` already set are skipped.

    Returns the Celery task ID so progress can be monitored.
    """
    task = fetch_bill_texts.delay()
    col = _get_col()
    pending = col.count_documents({"fullTextFetchedAt": None})
    return {
        "task_id": task.id,
        "message": "Text fetching queued",
        "pending_bills": pending,
    }


# ── Vectorization ─────────────────────────────────────────────────────────────

@app.post("/api/legislation/vectorize", tags=["Enrichment"])
def trigger_vectorize(
    _key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """Queue a background task to embed all un-vectorized bills into pgvector.

    Processes every bill in MongoDB where ``vectorizedAt`` is null. For bills
    that have ``fullText``, that content is chunked and embedded directly. For
    bills without full text, a structured prose document is assembled from
    metadata fields (title, subjects, sponsors, action history, etc.).

    Idempotent — bills with ``vectorizedAt`` already set are skipped.
    Returns the Celery task ID and the current pending count.
    """
    task = vectorize_bills.delay()
    col = _get_col()
    pending = col.count_documents({"vectorizedAt": None})
    return {
        "task_id": task.id,
        "message": "Vectorization queued",
        "pending_bills": pending,
    }


