"""Bill text assembler and LangChain document chunker.

For bills that have a ``fullText`` field (from the text-fetching pipeline),
that content is used directly and split into overlapping chunks. For bills
without full text, a structured prose document is assembled from the available
metadata fields (title, jurisdiction, session, subjects, sponsor names, action
history, and abstracts).

Chunking uses ``RecursiveCharacterTextSplitter`` calibrated to the tokenizer
of ``sentence-transformers/all-MiniLM-L6-v2``, producing chunks of 400 tokens
with 50-token overlap. Each returned ``Document`` carries bill metadata so the
vector store can store and filter on it.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_CHUNK_SIZE = 400
_CHUNK_OVERLAP = 50

_splitter: RecursiveCharacterTextSplitter | None = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    global _splitter
    if _splitter is None:
        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
            _splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                tokenizer,
                chunk_size=_CHUNK_SIZE,
                chunk_overlap=_CHUNK_OVERLAP,
            )
            logger.info("RecursiveCharacterTextSplitter initialised with %s tokenizer", _MODEL_NAME)
        except Exception as exc:
            logger.warning("Tokenizer not available (%s); falling back to char-based splitter", exc)
            _splitter = RecursiveCharacterTextSplitter(
                chunk_size=_CHUNK_SIZE * 4,
                chunk_overlap=_CHUNK_OVERLAP * 4,
            )
    return _splitter


def assemble_bill_text(doc: dict[str, Any]) -> str:
    """Build a searchable prose document from available bill metadata fields.

    Combines title, state/session/type, subjects, sponsor names, the latest
    action description, up to ten action history descriptions, and the first
    abstract (if any). Returns an empty string when there is nothing useful.

    Args:
        doc: A MongoDB legislation document.

    Returns:
        Multi-line prose string ready to be chunked and embedded.
    """
    parts: list[str] = []

    title = (doc.get("title") or "").strip()
    if title:
        parts.append(f"Title: {title}")

    jurisdiction = (doc.get("jurisdictionName") or "").strip()
    session = (doc.get("session") or "").strip()
    classification = doc.get("classification") or []
    if isinstance(classification, list):
        classification_str = ", ".join(classification)
    else:
        classification_str = str(classification)

    if jurisdiction or session:
        parts.append(
            f"State: {jurisdiction}. Session: {session}. Type: {classification_str}."
        )

    subjects = doc.get("subjects") or []
    if isinstance(subjects, list) and subjects:
        clean = [s for s in subjects if s and not str(s).startswith("(")][:10]
        if clean:
            parts.append(f"Subjects: {', '.join(str(s) for s in clean)}.")

    sponsors = doc.get("sponsors") or []
    if isinstance(sponsors, list) and sponsors:
        names = [s.get("name") for s in sponsors if isinstance(s, dict) and s.get("name")][:5]
        if names:
            parts.append(f"Sponsors: {', '.join(names)}.")

    latest_action = (doc.get("latestActionDescription") or "").strip()
    if latest_action:
        parts.append(f"Latest action: {latest_action}.")

    history = doc.get("history") or []
    if isinstance(history, list):
        descs = [
            a.get("description", "")
            for a in history
            if isinstance(a, dict) and a.get("description")
        ][:10]
        if descs:
            parts.append("Action history: " + " | ".join(descs) + ".")

    abstracts = doc.get("abstracts") or []
    if isinstance(abstracts, list):
        for entry in abstracts:
            text = entry.get("abstract") if isinstance(entry, dict) else None
            if text and isinstance(text, str) and text.strip():
                parts.append(f"Abstract: {text.strip()}")
                break

    return "\n\n".join(parts)


def chunk_bill(doc: dict[str, Any]) -> list[Document]:
    """Convert a MongoDB bill document into a list of LangChain ``Document`` objects.

    Priority:
    1. If ``fullText`` is present and non-trivial (> 100 chars), chunk it.
    2. Otherwise, assemble a prose document from metadata and chunk that.

    Each returned Document carries metadata:
    ``bill_id``, ``jurisdiction_id``, ``jurisdiction_name``, ``title``,
    ``classification``, ``session``.

    Args:
        doc: A MongoDB legislation document.

    Returns:
        List of ``Document`` objects (may be empty if no usable text).
    """
    full_text = (doc.get("fullText") or "").strip()
    if full_text and len(full_text) > 100:
        text_to_chunk = full_text
    else:
        text_to_chunk = assemble_bill_text(doc)

    if not text_to_chunk.strip():
        return []

    classification = doc.get("classification") or []
    if isinstance(classification, list):
        classification_str = ", ".join(str(c) for c in classification)
    else:
        classification_str = str(classification)

    metadata = {
        "bill_id": doc.get("id", ""),
        "jurisdiction_id": doc.get("jurisdictionId", ""),
        "jurisdiction_name": doc.get("jurisdictionName", ""),
        "title": (doc.get("title") or "")[:500],
        "classification": classification_str,
        "session": doc.get("session") or "",
    }

    splitter = _get_splitter()
    chunks = splitter.create_documents([text_to_chunk], metadatas=[metadata])
    return chunks
