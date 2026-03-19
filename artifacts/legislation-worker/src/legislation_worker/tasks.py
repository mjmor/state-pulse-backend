"""Celery tasks for syncing legislation from OpenStates."""

import logging
from datetime import datetime, timedelta, timezone

from .celery_app import app
from .config import JURISDICTIONS, SYNC_LOOKBACK_HOURS
from .db import ensure_indexes, get_collection, upsert_legislation
from .openstates import fetch_bills_since

logger = logging.getLogger(__name__)


@app.task(bind=True, name="legislation_worker.tasks.sync_legislation", max_retries=3)
def sync_legislation(self) -> dict:
    """Fetch bills updated in the last SYNC_LOOKBACK_HOURS and upsert to MongoDB.

    Runs once per day via Celery Beat (configured in celeryconfig.py).
    """
    updated_since = datetime.now(tz=timezone.utc) - timedelta(hours=SYNC_LOOKBACK_HOURS)
    logger.info(
        "Starting sync — updated_since=%s jurisdictions=%s",
        updated_since.isoformat(),
        JURISDICTIONS,
    )

    collection = get_collection()
    ensure_indexes(collection)

    total_fetched = 0
    total_upserted = 0
    errors: list[str] = []

    for jurisdiction in JURISDICTIONS:
        fetched = 0
        upserted = 0
        logger.info("Syncing jurisdiction: %s", jurisdiction)

        try:
            bills = fetch_bills_since(jurisdiction, updated_since)
            for bill in bills:
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

    summary = {
        "updated_since": updated_since.isoformat(),
        "jurisdictions": len(JURISDICTIONS),
        "total_fetched": total_fetched,
        "total_upserted": total_upserted,
        "errors": len(errors),
        "error_details": errors[:20],
    }
    logger.info("Sync complete: %s", summary)
    return summary
