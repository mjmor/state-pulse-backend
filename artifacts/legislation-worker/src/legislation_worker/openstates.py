"""OpenStates API v3 client — fetches bills with configurable filters."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Generator

import httpx

from .config import (
    OPENSTATES_API_KEY,
    OPENSTATES_BASE_URL,
    PAGE_SIZE,
)

logger = logging.getLogger(__name__)

BILL_INCLUDE = "abstracts,actions,sponsorships,versions,sources"

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_RETRY_DELAYS = [5, 15, 30]


def _make_client() -> httpx.Client:
    return httpx.Client(
        base_url=OPENSTATES_BASE_URL,
        headers={"X-API-KEY": OPENSTATES_API_KEY},
        timeout=_DEFAULT_TIMEOUT,
    )


def _get_with_retry(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET with retry logic for rate limits and transient errors."""
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            logger.warning("Retrying %s in %ds (attempt %d)", path, delay, attempt + 1)
            time.sleep(delay)
        try:
            response = client.get(path, params=params)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", delay or 60))
                logger.warning("Rate limited — sleeping %ds", retry_after)
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            logger.error("Timeout on %s: %s", path, exc)
            if attempt == len(_RETRY_DELAYS):
                raise
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP error on %s: %s", path, exc)
            raise
    raise RuntimeError(f"All retries exhausted for {path}")


def fetch_bills(
    jurisdiction: str,
    updated_since: datetime | None = None,
    subject: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield all bills for a jurisdiction, with optional time and subject filters.

    Args:
        jurisdiction: State abbreviation or OpenStates jurisdiction ID.
        updated_since: Only return bills updated after this datetime.
                       Pass ``None`` to fetch all bills regardless of date
                       (equivalent to "all time" — use carefully on large states).
        subject: Policy area / legislative subject to filter by
                 (e.g. "energy", "health", "education"). ``None`` means no filter.
    """
    page = 1

    with _make_client() as client:
        while True:
            params: dict[str, Any] = {
                "jurisdiction": jurisdiction,
                "include": BILL_INCLUDE,
                "page": page,
                "per_page": PAGE_SIZE,
                "sort": "updated_desc",
            }

            if updated_since is not None:
                params["updated_since"] = (
                    updated_since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                )

            if subject is not None:
                params["subject"] = subject

            logger.debug(
                "Fetching jurisdiction=%s page=%d updated_since=%s subject=%s",
                jurisdiction,
                page,
                params.get("updated_since", "ALL"),
                subject or "ANY",
            )

            data = _get_with_retry(client, "/bills", params)

            results: list[dict[str, Any]] = data.get("results", [])
            if not results:
                break

            yield from results

            meta: dict[str, Any] = data.get("pagination", {})
            max_page: int = meta.get("max_page", 1)
            logger.info(
                "Fetched %d bills from %s (page %d/%d) subject=%s",
                len(results),
                jurisdiction,
                page,
                max_page,
                subject or "ANY",
            )

            if page >= max_page:
                break
            page += 1


# Backwards-compatible alias used by the scheduled task
def fetch_bills_since(
    jurisdiction: str,
    updated_since: datetime,
    subject: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Alias for fetch_bills with a required updated_since for backwards compatibility."""
    return fetch_bills(jurisdiction, updated_since=updated_since, subject=subject)
