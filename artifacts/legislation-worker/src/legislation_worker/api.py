"""FastAPI REST layer over the MongoDB legislation collection."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.collection import Collection

from .db import get_collection

app = FastAPI(
    title="Legislation API",
    description="Query US state legislation synced from OpenStates.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert MongoDB document to JSON-serialisable dict."""
    doc.pop("_id", None)
    for key, value in doc.items():
        if isinstance(value, datetime):
            doc[key] = value.isoformat()
    return doc


def _get_col() -> Collection:
    return get_collection()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health() -> dict[str, str]:
    """Returns ok when MongoDB is reachable."""
    try:
        _get_col().database.client.admin.command("ping")
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MongoDB unavailable: {exc}")


# ── Jurisdictions ─────────────────────────────────────────────────────────────

@app.get("/api/jurisdictions", tags=["Legislation"])
def list_jurisdictions() -> list[dict[str, str]]:
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
    jurisdiction: str | None = Query(None, description="Jurisdiction ID (e.g. 'ocd-jurisdiction/country:us/state:ca/government')"),
    session: str | None = Query(None, description="Legislative session string"),
    classification: str | None = Query(None, description="Bill classification (e.g. 'bill', 'resolution')"),
    q: str | None = Query(None, description="Full-text search on title"),
    updated_since: str | None = Query(None, description="ISO 8601 datetime — only bills updated after this date"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Results per page (max 100)"),
) -> dict[str, Any]:
    """List legislation with optional filters and pagination."""
    col = _get_col()

    filt: dict[str, Any] = {}
    if jurisdiction:
        filt["jurisdictionId"] = jurisdiction
    if session:
        filt["session"] = session
    if classification:
        filt["classification"] = classification
    if q:
        filt["title"] = {"$regex": q, "$options": "i"}
    if updated_since:
        try:
            dt = datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid updated_since format — use ISO 8601")
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
        "pages": max(1, -(-total // limit)),  # ceiling division
        "results": [_serialize(d) for d in docs],
    }


# ── Single bill ───────────────────────────────────────────────────────────────

@app.get("/api/legislation/{bill_id:path}", tags=["Legislation"])
def get_legislation(bill_id: str) -> dict[str, Any]:
    """Fetch a single legislation document by its OpenStates ID."""
    col = _get_col()
    doc = col.find_one({"id": bill_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found")
    return _serialize(doc)
