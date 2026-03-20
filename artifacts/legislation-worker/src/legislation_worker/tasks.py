"""Celery tasks for syncing legislation from OpenStates."""

import logging
import time
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Any

from .celery_app import app
from .config import JURISDICTIONS, SUBJECT_FILTER, SYNC_LOOKBACK
from .db import ensure_indexes, get_collection, upsert_legislation
from .openstates import fetch_bills
from .text_fetcher import fetch_plain_text, pick_best_html_url

logger = logging.getLogger(__name__)

_TEXT_BATCH_SIZE = 50
_INTER_REQUEST_DELAY = 0.1


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
    result = _run_sync(JURISDICTIONS, updated_since, SUBJECT_FILTER)
    fetch_bill_texts.delay()
    return result


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
    result = _run_sync(jurisdictions, updated_since, subject)
    fetch_bill_texts.delay()
    return result


@app.task(bind=True, name="legislation_worker.tasks.fetch_bill_texts", max_retries=0)
def fetch_bill_texts(
    self,
    bill_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch and store plain-text bill content from state legislature HTML pages.

    Processes all bills where ``fullTextFetchedAt`` is null (not yet fetched)
    unless a specific list of ``bill_ids`` is given.  Bills are processed in
    explicit batches of ``_TEXT_BATCH_SIZE`` with a short inter-request delay
    to avoid hammering legislature servers.

    Failure handling:
    - ``no_html_url``: terminal skip (no HTML will ever exist). Sets
      ``fullTextFetchedAt`` so the bill is not re-queued on future runs.
    - HTTP/network errors: transient. ``fullTextFetchedAt`` is left null so
      the bill will be retried on the next run. Only ``fullTextFetchError``
      is updated with the last error message.

    Args:
        bill_ids: Optional list of OpenStates bill IDs to restrict processing.
                  When None, all un-fetched bills with at least one version
                  are processed.

    Returns:
        A summary dict with counts for fetched, skipped, and failed bills.
    """
    collection = get_collection()
    now = datetime.now(tz=timezone.utc)

    if bill_ids is not None:
        query: dict[str, Any] = {"id": {"$in": bill_ids}, "versions": {"$ne": []}}
    else:
        query = {
            "fullTextFetchedAt": None,
            "versions": {"$exists": True, "$ne": []},
        }

    all_ids: list[str] = [doc["id"] for doc in collection.find(query, {"id": 1})]
    total = len(all_ids)
    logger.info("fetch_bill_texts: %d bills to process in batches of %d", total, _TEXT_BATCH_SIZE)

    success_count = 0
    skip_count = 0
    fail_count = 0
    processed = 0

    id_iter = iter(all_ids)
    batch_num = 0
    while True:
        batch = list(islice(id_iter, _TEXT_BATCH_SIZE))
        if not batch:
            break
        batch_num += 1

        batch_docs = list(collection.find({"id": {"$in": batch}}, {"id": 1, "versions": 1}))

        for doc in batch_docs:
            bill_id: str = doc["id"]
            versions: list[dict[str, Any]] = doc.get("versions", [])

            url = pick_best_html_url(versions)
            if not url:
                collection.update_one(
                    {"id": bill_id},
                    {"$set": {
                        "fullTextFetchedAt": now,
                        "fullTextFetchError": "no_html_url",
                    }},
                )
                skip_count += 1
                processed += 1
                continue

            try:
                text = fetch_plain_text(url)
                collection.update_one(
                    {"id": bill_id},
                    {"$set": {
                        "fullText": text,
                        "fullTextUrl": url,
                        "fullTextFetchedAt": now,
                        "fullTextFetchError": None,
                    }},
                )
                success_count += 1
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.warning("Text fetch failed for %s (%s): %s", bill_id, url, error_msg)
                collection.update_one(
                    {"id": bill_id},
                    {"$set": {"fullTextFetchError": error_msg}},
                )
                fail_count += 1

            processed += 1
            time.sleep(_INTER_REQUEST_DELAY)

        logger.info(
            "fetch_bill_texts batch %d done — progress: %d/%d success=%d skip=%d fail=%d",
            batch_num,
            processed,
            total,
            success_count,
            skip_count,
            fail_count,
        )

    summary = {
        "total": total,
        "success": success_count,
        "skipped_no_html": skip_count,
        "failed": fail_count,
    }
    logger.info("fetch_bill_texts complete: %s", summary)
    return summary
