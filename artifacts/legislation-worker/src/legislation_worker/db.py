"""MongoDB connection and legislation upsert helpers."""

import logging
from datetime import datetime, timezone
from typing import Any

import pymongo
from pymongo import MongoClient
from pymongo.collection import Collection

from .config import MONGODB_DB, MONGODB_URI

logger = logging.getLogger(__name__)

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    return _client


def get_collection() -> Collection:
    client = get_client()
    db = client[MONGODB_DB]
    return db["legislation"]


def ensure_indexes(collection: Collection) -> None:
    """Create required indexes if they don't already exist."""
    collection.create_index("id", unique=True, background=True)
    collection.create_index("jurisdictionId", background=True)
    collection.create_index("latestActionAt", background=True)
    collection.create_index("updatedAt", background=True)
    logger.info("MongoDB indexes ensured on 'legislation' collection")


def _parse_date(value: str | None) -> datetime | None:
    """Convert an ISO date string to a datetime, or return None."""
    if not value:
        return None
    # fromisoformat handles all ISO 8601 variants including microseconds and
    # timezone offsets (Python 3.11+).  Fall back to manual strptime for
    # the bare-Z suffix variant that older Pythons don't support.
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    logger.warning("Could not parse date: %r", value)
    return None


def _map_sponsor(sponsorship: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": sponsorship.get("name"),
        "classification": sponsorship.get("classification"),
        "entityType": sponsorship.get("entity_type"),
        "primary": sponsorship.get("primary", False),
        "personId": (sponsorship.get("person") or {}).get("id"),
        "organizationId": (sponsorship.get("organization") or {}).get("id"),
    }


def _map_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": action.get("date"),
        "description": action.get("description"),
        "order": action.get("order"),
        "classification": action.get("classification", []),
        "organization": (action.get("organization") or {}).get("name"),
        "relatedEntities": action.get("related_entities", []),
    }


def _map_version(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "note": doc.get("note"),
        "date": doc.get("date"),
        "links": [{"url": lnk.get("url"), "mediaType": lnk.get("media_type")} for lnk in doc.get("links", [])],
    }


def _map_source(link: dict[str, Any]) -> dict[str, Any]:
    return {"url": link.get("url"), "note": link.get("note")}


def _map_abstract(abstract: dict[str, Any]) -> dict[str, Any]:
    return {"abstract": abstract.get("abstract"), "note": abstract.get("note")}


def bill_to_legislation(bill: dict[str, Any]) -> dict[str, Any]:
    """Map an OpenStates Bill API response to the Legislation document schema."""

    jurisdiction: dict[str, Any] = bill.get("jurisdiction") or {}
    from_org: dict[str, Any] = bill.get("from_organization") or {}
    chamber_name: str | None = from_org.get("name") or from_org.get("classification")

    now = datetime.now(tz=timezone.utc)

    state_url: str = bill.get("openstates_url", "")

    doc: dict[str, Any] = {
        "id": bill["id"],
        "identifier": bill.get("identifier"),
        "title": bill.get("title"),
        "session": bill.get("session"),
        "jurisdictionId": jurisdiction.get("id"),
        "jurisdictionName": jurisdiction.get("name"),
        "chamber": chamber_name,
        "classification": bill.get("classification", []),
        "subjects": bill.get("subject", []),
        "statusText": None,
        "sponsors": [_map_sponsor(s) for s in bill.get("sponsorships", [])],
        "history": [_map_action(a) for a in bill.get("actions", [])],
        "versions": [_map_version(v) for v in bill.get("versions", [])],
        "sources": [_map_source(s) for s in bill.get("sources", [])],
        "abstracts": [_map_abstract(a) for a in bill.get("abstracts", [])],
        "openstatesUrl": bill.get("openstates_url"),
        "stateLegislatureUrl": state_url,
        "congressUrl": None,
        "firstActionAt": _parse_date(bill.get("first_action_date")),
        "latestActionAt": _parse_date(bill.get("latest_action_date")),
        "latestActionDescription": bill.get("latest_action_description") or None,
        "latestPassageAt": _parse_date(bill.get("latest_passage_date")),
        "createdAt": _parse_date(bill.get("created_at")) or now,
        "updatedAt": _parse_date(bill.get("updated_at")) or now,
        "extras": bill.get("extras") or None,
        "fullText": None,
        "geminiSummary": None,
        "longGeminiSummary": None,
        "geminiSummarySource": None,
        "summary": None,
        "topicClassification": None,
        "enactedAt": None,
        "enactedFieldUpdatedAt": now,
    }

    return doc


def upsert_legislation(collection: Collection, bill: dict[str, Any]) -> bool:
    """Map and upsert a bill. Returns True if the document was inserted/modified."""
    doc = bill_to_legislation(bill)
    bill_id = doc["id"]

    result = collection.update_one(
        {"id": bill_id},
        {"$set": doc},
        upsert=True,
    )
    return result.upserted_id is not None or result.modified_count > 0
