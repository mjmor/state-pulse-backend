"""Embedding layer using sentence-transformers/all-MiniLM-L6-v2.

Exposes a module-level singleton ``HuggingFaceEmbeddings`` instance so the
model is only loaded once per worker process. Provides ``embed_chunks`` for
batch-encoding a list of LangChain ``Document`` objects.

Model outputs 384-dimensional L2-normalised vectors suitable for cosine
similarity search in pgvector.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.documents import Document

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_embeddings = None


def _get_embeddings():
    """Return the singleton HuggingFaceEmbeddings instance, loading it on first call."""
    global _embeddings
    if _embeddings is None:
        logger.info("Loading embedding model '%s' (first-time download may take a moment)...", _MODEL_NAME)
        from langchain_huggingface import HuggingFaceEmbeddings

        _embeddings = HuggingFaceEmbeddings(
            model_name=_MODEL_NAME,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("Embedding model loaded successfully.")
    return _embeddings


def embed_chunks(chunks: list[Document]) -> list[list[float]]:
    """Embed a list of LangChain Documents using MiniLM-L6-v2.

    Args:
        chunks: List of ``Document`` objects whose ``page_content`` will be embedded.

    Returns:
        Parallel list of 384-dimensional embedding vectors (as lists of floats).
        Returns an empty list when ``chunks`` is empty.
    """
    if not chunks:
        return []
    texts = [chunk.page_content for chunk in chunks]
    model = _get_embeddings()
    return model.embed_documents(texts)


def embed_query(query_text: str) -> list[float]:
    """Embed a single query string for similarity search.

    Args:
        query_text: Natural-language search query.

    Returns:
        384-dimensional embedding vector as a list of floats.
    """
    model = _get_embeddings()
    return model.embed_query(query_text)
