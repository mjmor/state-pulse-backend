"""Celery tasks for syncing legislation from OpenStates."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .celery_app import app
from .config import JURISDICTIONS, SUBJECT_FILTER, SYNC_LOOKBACK
from .db import ensure_indexes, get_collection, upsert_legislation
from .openstates import fetch_bills

logger = logging.getLogger(__name__)


def _resolve_updated_since(lookback: str) -> datetime | None:
    """Convert a lookback setting to a datetime (or None for 'all time').

    Args:
        lookback: Either "all" (no date filter) or an integer number of hours
                  as a string (e.g. "24", "168").

    Returns:
        A timezone-aware datetime, or None if lookback is "all".
    """
    if lookback.strip().lower() == "all":
        return None
    return datetime.now(tz=timezone.utc) - timedelta(hours=int(lookback))


def _run_sync(
    jurisdictions: list[str],
    updated_since: datetime | None,
    subject: str | None,
) -> dict[str, Any]:
    """Core sync logic — upserts bills from OpenStates into MongoDB."""
    collection = get_collection()
    ensure_indexes(collection)

    total_fetched = 0
    total_upserted = 0
    errors: list[str] = []

    for jurisdiction in jurisdictions:
        fetched = 0
        upserted = 0
        logger.info(
            "Syncing jurisdiction=%s updated_since=%s subject=%s",
            jurisdiction,
            updated_since.isoformat() if updated_since else "ALL",
            subject or "ANY",
        )

        try:
            for bill in fetch_bills(jurisdiction, updated_since=updated_since, subject=subject):
                fetched += 1
                bill_id = bill.get("id", "unknown")
                try:
                    if upsert_legislation(collection, bill):
                        upserted += 1
                except Exception as exc:
                    msg = f"upsert {jurisdiction}/{bill_id}: {exc}"
                    logger.error("Upsert failed — %s", msg)
                    errors.append(msg)
        except Exception as exc:
            msg = f"fetch error for {jurisdiction}: {type(exc).__name__}: {exc}"
            logger.error(msg)
            errors.append(msg)

        logger.info(
            "Finished %s — fetched=%d upserted=%d",
            jurisdiction,
            fetched,
            upserted,
        )
        total_fetched += fetched
        total_upserted += upserted

    summary: dict[str, Any] = {
        "updated_since": updated_since.isoformat() if updated_since else "ALL",
        "subject": subject or "ANY",
        "jurisdictions": len(jurisdictions),
        "total_fetched": total_fetched,
        "total_upserted": total_upserted,
        "errors": len(errors),
        "error_details": errors[:20],
    }
    logger.info("Sync complete: %s", summary)
    return summary


@app.task(bind=True, name="legislation_worker.tasks.sync_legislation", max_retries=3)
def sync_legislation(self) -> dict:
    """Scheduled daily sync — uses SYNC_LOOKBACK and SUBJECT_FILTER from config."""
    updated_since = _resolve_updated_since(SYNC_LOOKBACK)
    logger.info(
        "Starting scheduled sync — lookback=%s updated_since=%s jurisdictions=%s subject=%s",
        SYNC_LOOKBACK,
        updated_since.isoformat() if updated_since else "ALL",
        JURISDICTIONS,
        SUBJECT_FILTER or "ANY",
    )
    return _run_sync(JURISDICTIONS, updated_since, SUBJECT_FILTER)


@app.task(bind=True, name="legislation_worker.tasks.one_time_sync", max_retries=0)
def one_time_sync(
    self,
    jurisdictions: list[str],
    lookback: str = "all",
    subject: str | None = None,
) -> dict:
    """One-off sync with explicit parameters.

    Args:
        jurisdictions: List of state abbreviations to sync.
        lookback: "all" or number of hours as a string (e.g. "168").
        subject: Policy area filter (e.g. "energy"). None means no filter.
    """
    updated_since = _resolve_updated_since(lookback)
    logger.info(
        "Starting one-time sync — jurisdictions=%s lookback=%s updated_since=%s subject=%s",
        jurisdictions,
        lookback,
        updated_since.isoformat() if updated_since else "ALL",
        subject or "ANY",
    )
    return _run_sync(jurisdictions, updated_since, subject)
