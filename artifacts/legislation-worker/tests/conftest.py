import os

os.environ.setdefault("OPENSTATES_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test_state_pulse")

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def celery_eager():
    from legislation_worker.celery_app import app
    app.conf.update(task_always_eager=True, task_eager_propagates=True)
    yield
    app.conf.update(task_always_eager=False, task_eager_propagates=False)


@pytest.fixture
def mock_collection(mocker):
    col = MagicMock()
    mocker.patch("legislation_worker.tasks.get_collection", return_value=col)
    mocker.patch("legislation_worker.tasks.ensure_indexes")
    return col


def make_bill(bill_id="ocd-bill/test-1", **kwargs):
    base = {
        "id": bill_id,
        "identifier": "HB 1",
        "title": "A test bill",
        "jurisdictionId": "ocd-jurisdiction/country:us/state:mi/government",
        "jurisdictionName": "Michigan",
        "session": "2025",
        "classification": ["bill"],
        "chamber": "House",
        "subjects": [],
        "versions": [
            {
                "date": "2025-01-01",
                "note": "Introduced",
                "links": [{"url": "https://example.com/bill.html", "mediaType": "text/html"}],
            }
        ],
        "fullText": None,
        "fullTextFetchedAt": None,
        "vectorizedAt": None,
    }
    base.update(kwargs)
    return base
