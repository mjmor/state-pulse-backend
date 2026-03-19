"""OpenStates API v3 client — fetches bills updated within a time window."""

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


def fetch_bills_since(
    jurisdiction: str,
    updated_since: datetime,
) -> Generator[dict[str, Any], None, None]:
    """Yield all bills for a jurisdiction updated since *updated_since*.

    The ``updated_since`` param is sent as an ISO-8601 timestamp. Pagination
    is handled automatically by following the ``meta.page`` cursor until all
    pages have been consumed.
    """
    since_str = updated_since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = 1

    with _make_client() as client:
        while True:
            params: dict[str, Any] = {
                "jurisdiction": jurisdiction,
                "updated_since": since_str,
                "include": BILL_INCLUDE,
                "page": page,
                "per_page": PAGE_SIZE,
                "sort": "updated_desc",
            }

            logger.debug(
                "Fetching jurisdiction=%s page=%d updated_since=%s",
                jurisdiction,
                page,
                since_str,
            )

            data = _get_with_retry(client, "/bills", params)

            results: list[dict[str, Any]] = data.get("results", [])
            if not results:
                break

            yield from results

            meta: dict[str, Any] = data.get("pagination", {})
            max_page: int = meta.get("max_page", 1)
            logger.info(
                "Fetched %d bills from %s (page %d/%d)",
                len(results),
                jurisdiction,
                page,
                max_page,
            )

            if page >= max_page:
                break
            page += 1
