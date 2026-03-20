"""API key authentication for the FastAPI legislation server.

Keys are stored as bcrypt hashes in PostgreSQL. Each request passes the
raw key in the `X-API-Key` header; we hash it and compare against the DB.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from functools import lru_cache

import psycopg2
import psycopg2.extras
from fastapi import Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

logger = logging.getLogger(__name__)

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")


def _get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of the raw key (fast, good enough for server-side lookup)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash). Store the hash; give the raw key to the user."""
    raw = secrets.token_urlsafe(32)
    return raw, _hash_key(raw)


def create_key_in_db(name: str, description: str = "", scopes: list[str] | None = None) -> str:
    """Generate and persist a new API key. Returns the raw key (shown once)."""
    scopes = scopes or ["read"]
    raw, key_hash = generate_api_key()
    prefix = raw[:8]

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_keys (name, key_hash, key_prefix, scopes, description)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (name, key_hash, prefix, scopes, description),
            )
        conn.commit()

    logger.info("Created API key '%s' (prefix=%s)", name, prefix)
    return raw


def verify_api_key(raw_key: str) -> dict:
    """Look up the hashed key in Postgres. Returns the key row or raises 401."""
    key_hash = _hash_key(raw_key)

    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, scopes, is_active
                FROM api_keys
                WHERE key_hash = %s
                """,
                (key_hash,),
            )
            row = cur.fetchone()

        if not row or not row["is_active"]:
            return None

        # Update last_used_at non-blockingly
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET last_used_at = %s WHERE key_hash = %s",
                (datetime.now(tz=timezone.utc), key_hash),
            )
        conn.commit()

    return dict(row)


async def require_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> dict:
    """FastAPI dependency — enforces X-API-Key header authentication."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    key_row = verify_api_key(api_key)
    if not key_row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or inactive API key",
        )

    return key_row


def require_scope(scope: str):
    """Return a FastAPI dependency that enforces a required scope.

    Usage::

        @app.post("/admin/action")
        def action(_key: dict = Depends(require_scope("admin"))):
            ...
    """
    async def _check(key_row: dict = Depends(require_api_key)) -> dict:
        scopes: list = key_row.get("scopes") or []
        if scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have the required scope: '{scope}'",
            )
        return key_row

    return _check
