"""Fetch and extract plain text from bill HTML version pages."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_VERSION_PRIORITY: dict[str, int] = {
    "enrolled": 100,
    "chaptered": 100,
    "signed": 100,
    "engrossed": 80,
    "amended": 60,
    "substitute": 50,
    "introduced": 20,
    "filed": 10,
}

_FETCH_TIMEOUT = httpx.Timeout(25.0, connect=10.0)
_HEADERS = {
    "User-Agent": "LegislationETL/1.0 (research; +https://github.com/statepulse)",
    "Accept": "text/html,application/xhtml+xml",
}


def _version_score(note: str | None) -> int:
    """Return a numeric priority for a version note label."""
    if not note:
        return 0
    lower = note.lower().strip()
    for keyword, score in _VERSION_PRIORITY.items():
        if keyword in lower:
            return score
    return 5


def pick_best_html_url(versions: list[dict[str, Any]]) -> str | None:
    """Return the HTML URL from the most-advanced bill version.

    Selection logic:
    1. Score each version by its ``note`` label (Enrolled > Engrossed > Introduced …).
    2. Among versions with the same top score, prefer the one that appears last
       in the list (most recently added).
    3. Within the chosen version, prefer the link whose ``mediaType`` is
       ``text/html``; if none exists, skip the version entirely.

    Returns None when no HTML link can be found across any version.
    """
    if not versions:
        return None

    scored: list[tuple[int, int, str]] = []
    for idx, version in enumerate(versions):
        score = _version_score(version.get("note"))
        for link in version.get("links", []):
            media = (link.get("mediaType") or "").lower()
            url = link.get("url") or ""
            if "html" in media or url.lower().endswith(".htm") or url.lower().endswith(".html"):
                scored.append((score, idx, url))

    if not scored:
        return None

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def fetch_plain_text(url: str, timeout: float = 25.0) -> str:
    """Fetch an HTML bill page and return clean plain text.

    Strips all HTML tags, collapses whitespace, and removes boilerplate
    navigation / header / footer elements common on state legislature sites.

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
        httpx.TimeoutException: On a network timeout.
        ValueError: If the response body appears to be binary (PDF/image).
    """
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), headers=_HEADERS, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type or "octet-stream" in content_type:
        raise ValueError(f"Non-HTML content-type received: {content_type}")

    soup = BeautifulSoup(response.text, "lxml")

    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe", "form"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text
