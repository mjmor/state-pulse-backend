"""PostgreSQL pgvector store for bill chunk embeddings.

Manages the ``bill_chunks`` table (schema + IVFFlat index) and exposes two
public functions:

- ``ensure_schema()`` — idempotent DDL; called on API / worker startup.
- ``upsert_bill_vectors(bill_id, chunks, embeddings)`` — write chunks.
- ``semantic_search(query_text, k, jurisdiction, classification)`` — query.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

logger = logging.getLogger(__name__)

_DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bill_chunks (
    id            SERIAL PRIMARY KEY,
    bill_id       TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(384),
    jurisdiction_id   TEXT,
    jurisdiction_name TEXT,
    title         TEXT,
    classification TEXT,
    session       TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (bill_id, chunk_index)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS bill_chunks_embedding_idx
    ON bill_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
"""


def _get_conn() -> psycopg2.extensions.connection:
    """Return a psycopg2 connection with the pgvector type registered.

    Assumes the ``vector`` extension is already installed in the database.
    Call ``ensure_schema()`` first to guarantee this.
    """
    conn = psycopg2.connect(_DATABASE_URL)
    register_vector(conn)
    return conn


def ensure_schema() -> None:
    """Create the ``bill_chunks`` table and IVFFlat index if they don't exist.

    Opens a plain (non-vector-aware) connection first to run
    ``CREATE EXTENSION IF NOT EXISTS vector``, then reconnects with the
    pgvector type registered to create the table and index.

    Safe to call multiple times (all statements are idempotent).
    """
    # Phase 1: ensure the extension exists (plain connection, vector type may not be registered yet)
    conn = psycopg2.connect(_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_EXTENSION)
        conn.commit()
    finally:
        conn.close()

    # Phase 2: create table and index with vector type available
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE)
            cur.execute(_CREATE_INDEX)
        conn.commit()
        logger.info("bill_chunks schema ensured in PostgreSQL")
    finally:
        conn.close()


def upsert_bill_vectors(
    bill_id: str,
    chunks: list,
    embeddings: list[list[float]],
) -> int:
    """Upsert bill chunk vectors into ``bill_chunks``.

    Uses ``INSERT … ON CONFLICT (bill_id, chunk_index) DO UPDATE`` so
    re-running the task is safe.

    Args:
        bill_id:    OpenStates bill ID.
        chunks:     LangChain ``Document`` objects produced by ``chunk_bill``.
        embeddings: Parallel list of 384-dim vectors from ``embed_chunks``.

    Returns:
        Number of rows upserted.
    """
    if not chunks or not embeddings:
        return 0

    sql = """
        INSERT INTO bill_chunks (
            bill_id, chunk_index, content, embedding,
            jurisdiction_id, jurisdiction_name, title, classification, session
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bill_id, chunk_index) DO UPDATE SET
            content           = EXCLUDED.content,
            embedding         = EXCLUDED.embedding,
            jurisdiction_id   = EXCLUDED.jurisdiction_id,
            jurisdiction_name = EXCLUDED.jurisdiction_name,
            title             = EXCLUDED.title,
            classification    = EXCLUDED.classification,
            session           = EXCLUDED.session,
            created_at        = NOW()
    """

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for idx, (chunk, vec) in enumerate(zip(chunks, embeddings)):
                meta = chunk.metadata
                cur.execute(
                    sql,
                    (
                        bill_id,
                        idx,
                        chunk.page_content,
                        vec,
                        meta.get("jurisdiction_id", ""),
                        meta.get("jurisdiction_name", ""),
                        meta.get("title", ""),
                        meta.get("classification", ""),
                        meta.get("session", ""),
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    return len(chunks)


def semantic_search(
    query_text: str,
    k: int = 10,
    jurisdiction: str | None = None,
    classification: str | None = None,
) -> list[dict[str, Any]]:
    """Return top-k bills semantically similar to ``query_text``.

    Embeds the query, runs a cosine-distance search in ``bill_chunks``,
    deduplicates by bill (best matching chunk per bill), and returns results
    sorted by descending similarity.

    Args:
        query_text:     Natural language query.
        k:              Maximum number of bill results to return.
        jurisdiction:   Optional OCD jurisdiction ID to restrict results.
        classification: Optional classification string filter (case-insensitive
                        substring match, e.g. ``"bill"``).

    Returns:
        List of dicts with keys:
        ``bill_id``, ``title``, ``jurisdiction_id``, ``jurisdiction_name``,
        ``classification``, ``session``, ``matched_content``, ``similarity``.
    """
    from .vectorizer import embed_query

    query_vec = embed_query(query_text)

    conditions: list[str] = []
    params: list[Any] = [query_vec, query_vec]

    if jurisdiction:
        conditions.append("jurisdiction_id = %s")
        params.append(jurisdiction)
    if classification:
        conditions.append("classification ILIKE %s")
        params.append(f"%{classification}%")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        WITH ranked AS (
            SELECT
                bill_id,
                title,
                jurisdiction_id,
                jurisdiction_name,
                classification,
                session,
                content,
                1 - (embedding <=> %s::vector) AS similarity,
                ROW_NUMBER() OVER (
                    PARTITION BY bill_id
                    ORDER BY embedding <=> %s::vector
                ) AS rn
            FROM bill_chunks
            {where_clause}
        )
        SELECT bill_id, title, jurisdiction_id, jurisdiction_name,
               classification, session, content, similarity
        FROM ranked
        WHERE rn = 1
        ORDER BY similarity DESC
        LIMIT %s
    """

    params.append(k)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "bill_id": row[0],
            "title": row[1],
            "jurisdiction_id": row[2],
            "jurisdiction_name": row[3],
            "classification": row[4],
            "session": row[5],
            "matched_content": row[6],
            "similarity": round(float(row[7]), 4),
        }
        for row in rows
    ]
