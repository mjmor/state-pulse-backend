from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest
from freezegun import freeze_time

from tests.conftest import make_bill


class TestResolveUpdatedSince:
    def test_all_returns_none(self):
        from legislation_worker.tasks import _resolve_updated_since

        assert _resolve_updated_since("all") is None

    def test_all_case_insensitive(self):
        from legislation_worker.tasks import _resolve_updated_since

        assert _resolve_updated_since("ALL") is None
        assert _resolve_updated_since("All") is None

    @freeze_time("2026-03-30 12:00:00")
    def test_hours_lookback(self):
        from legislation_worker.tasks import _resolve_updated_since

        result = _resolve_updated_since("24")
        assert result == datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc)

    @freeze_time("2026-03-30 12:00:00")
    def test_zero_hours_returns_now(self):
        from legislation_worker.tasks import _resolve_updated_since

        result = _resolve_updated_since("0")
        assert result == datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc)


class TestSyncLegislation:
    def _patch(self, mocker, bills, upsert_return=True):
        mocker.patch("legislation_worker.tasks.JURISDICTIONS", new=["mi"])
        mock_fetch = mocker.patch(
            "legislation_worker.tasks.fetch_bills", return_value=iter(bills)
        )
        mocker.patch("legislation_worker.tasks.upsert_legislation", return_value=upsert_return)
        mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mocker.patch("legislation_worker.tasks.vectorize_bills.delay")
        return mock_fetch

    def test_happy_path(self, mock_collection, mocker):
        self._patch(mocker, [make_bill("b1"), make_bill("b2"), make_bill("b3")])

        from legislation_worker.tasks import sync_legislation

        result = sync_legislation.apply().get()

        assert result["total_fetched"] == 3
        assert result["total_upserted"] == 3
        assert result["errors"] == 0

    def test_triggers_downstream_tasks(self, mock_collection, mocker):
        mocker.patch("legislation_worker.tasks.JURISDICTIONS", new=["mi"])
        mocker.patch("legislation_worker.tasks.fetch_bills", return_value=iter([]))
        mocker.patch("legislation_worker.tasks.upsert_legislation", return_value=True)
        mock_texts = mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mock_vectors = mocker.patch("legislation_worker.tasks.vectorize_bills.delay")

        from legislation_worker.tasks import sync_legislation

        sync_legislation.apply()

        mock_texts.assert_called_once()
        mock_vectors.assert_called_once()

    def test_partial_upsert_error(self, mock_collection, mocker):
        mocker.patch("legislation_worker.tasks.JURISDICTIONS", new=["mi"])
        mocker.patch(
            "legislation_worker.tasks.fetch_bills",
            return_value=iter([make_bill("b1"), make_bill("b2")]),
        )
        mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mocker.patch("legislation_worker.tasks.vectorize_bills.delay")
        mocker.patch(
            "legislation_worker.tasks.upsert_legislation",
            side_effect=[Exception("DB error"), True],
        )

        from legislation_worker.tasks import sync_legislation

        result = sync_legislation.apply().get()

        assert result["total_fetched"] == 2
        assert result["total_upserted"] == 1
        assert result["errors"] == 1
        assert "DB error" in result["error_details"][0]

    def test_empty_sync(self, mock_collection, mocker):
        self._patch(mocker, [])

        from legislation_worker.tasks import sync_legislation

        result = sync_legislation.apply().get()

        assert result["total_fetched"] == 0
        assert result["total_upserted"] == 0
        assert result["errors"] == 0


class TestOneTimeSync:
    def test_passes_jurisdictions_and_lookback(self, mock_collection, mocker):
        mock_fetch = mocker.patch(
            "legislation_worker.tasks.fetch_bills", return_value=iter([])
        )
        mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mocker.patch("legislation_worker.tasks.vectorize_bills.delay")

        from legislation_worker.tasks import one_time_sync

        result = one_time_sync.apply(
            kwargs={"jurisdictions": ["ca", "tx"], "lookback": "all"}
        ).get()

        assert result["jurisdictions"] == 2
        assert result["updated_since"] == "ALL"
        assert mock_fetch.call_count == 2

    def test_subject_filter_passed_through(self, mock_collection, mocker):
        mock_fetch = mocker.patch(
            "legislation_worker.tasks.fetch_bills", return_value=iter([])
        )
        mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mocker.patch("legislation_worker.tasks.vectorize_bills.delay")

        from legislation_worker.tasks import one_time_sync

        one_time_sync.apply(
            kwargs={"jurisdictions": ["mi"], "lookback": "all", "subject": "energy"}
        ).get()

        mock_fetch.assert_called_once_with("mi", updated_since=None, subject="energy")

    @freeze_time("2026-03-30 12:00:00")
    def test_lookback_hours_resolved(self, mock_collection, mocker):
        mock_fetch = mocker.patch(
            "legislation_worker.tasks.fetch_bills", return_value=iter([])
        )
        mocker.patch("legislation_worker.tasks.fetch_bill_texts.delay")
        mocker.patch("legislation_worker.tasks.vectorize_bills.delay")

        from legislation_worker.tasks import one_time_sync

        result = one_time_sync.apply(
            kwargs={"jurisdictions": ["mi"], "lookback": "48"}
        ).get()

        expected_dt = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        _, kwargs = mock_fetch.call_args
        assert kwargs["updated_since"] == expected_dt
        assert result["updated_since"] == expected_dt.isoformat()


class TestFetchBillTexts:
    def test_happy_path(self, mock_collection, mocker):
        mocker.patch("time.sleep")
        doc = make_bill("bill-1")
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]
        mocker.patch(
            "legislation_worker.tasks.pick_best_html_url",
            return_value="https://example.com/bill.html",
        )
        mocker.patch("legislation_worker.tasks.fetch_plain_text", return_value="Bill text content")

        from legislation_worker.tasks import fetch_bill_texts

        result = fetch_bill_texts.apply().get()

        assert result == {"total": 1, "success": 1, "skipped_no_html": 0, "failed": 0}
        set_doc = mock_collection.update_one.call_args[0][1]["$set"]
        assert set_doc["fullText"] == "Bill text content"
        assert set_doc["fullTextUrl"] == "https://example.com/bill.html"
        assert set_doc["fullTextFetchError"] is None

    def test_no_html_url_skipped(self, mock_collection, mocker):
        mocker.patch("time.sleep")
        doc = make_bill("bill-1", versions=[])
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]
        mocker.patch("legislation_worker.tasks.pick_best_html_url", return_value=None)

        from legislation_worker.tasks import fetch_bill_texts

        result = fetch_bill_texts.apply().get()

        assert result["skipped_no_html"] == 1
        assert result["success"] == 0
        set_doc = mock_collection.update_one.call_args[0][1]["$set"]
        assert set_doc["fullTextFetchError"] == "no_html_url"
        assert "fullTextFetchedAt" in set_doc

    def test_fetch_error_counted_as_failed(self, mock_collection, mocker):
        import httpx

        mocker.patch("time.sleep")
        doc = make_bill("bill-1")
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]
        mocker.patch(
            "legislation_worker.tasks.pick_best_html_url",
            return_value="https://example.com/bill.html",
        )
        mocker.patch(
            "legislation_worker.tasks.fetch_plain_text",
            side_effect=httpx.TimeoutException("timeout"),
        )

        from legislation_worker.tasks import fetch_bill_texts

        result = fetch_bill_texts.apply().get()

        assert result["failed"] == 1
        assert result["success"] == 0
        set_doc = mock_collection.update_one.call_args[0][1]["$set"]
        assert "TimeoutException" in set_doc["fullTextFetchError"]

    def test_bill_ids_filter_skips_fetched_at_check(self, mock_collection, mocker):
        mocker.patch("time.sleep")
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [make_bill("bill-1")],
        ]
        mocker.patch("legislation_worker.tasks.pick_best_html_url", return_value=None)

        from legislation_worker.tasks import fetch_bill_texts

        fetch_bill_texts.apply(kwargs={"bill_ids": ["bill-1"]}).get()

        first_query = mock_collection.find.call_args_list[0][0][0]
        assert "$in" in first_query.get("id", {})
        assert "fullTextFetchedAt" not in first_query

    def test_empty_collection(self, mock_collection, mocker):
        mock_collection.find.return_value = iter([])

        from legislation_worker.tasks import fetch_bill_texts

        result = fetch_bill_texts.apply().get()

        assert result == {"total": 0, "success": 0, "skipped_no_html": 0, "failed": 0}

    def test_multiple_bills_processed(self, mock_collection, mocker):
        mocker.patch("time.sleep")
        docs = [make_bill(f"bill-{i}") for i in range(3)]
        mock_collection.find.side_effect = [
            [{"id": d["id"]} for d in docs],
            docs,
        ]
        mocker.patch(
            "legislation_worker.tasks.pick_best_html_url",
            return_value="https://example.com/bill.html",
        )
        mocker.patch("legislation_worker.tasks.fetch_plain_text", return_value="text")

        from legislation_worker.tasks import fetch_bill_texts

        result = fetch_bill_texts.apply().get()

        assert result["total"] == 3
        assert result["success"] == 3
        assert mock_collection.update_one.call_count == 3


class TestVectorizeBills:
    def _patch_vector_deps(self, mocker, chunks=None, embeddings=None):
        if chunks is None:
            chunks = [MagicMock()]
        if embeddings is None:
            embeddings = [[0.1] * 384]
        mocker.patch("legislation_worker.vector_store.ensure_schema")
        mock_chunk = mocker.patch("legislation_worker.chunker.chunk_bill", return_value=chunks)
        mock_embed = mocker.patch("legislation_worker.vectorizer.embed_chunks", return_value=embeddings)
        mock_upsert = mocker.patch(
            "legislation_worker.vector_store.upsert_bill_vectors", return_value=len(chunks)
        )
        return mock_chunk, mock_embed, mock_upsert

    def test_happy_path(self, mock_collection, mocker):
        chunks = [MagicMock()]
        embeddings = [[0.1] * 384]
        mock_chunk, mock_embed, mock_upsert = self._patch_vector_deps(
            mocker, chunks, embeddings
        )
        doc = make_bill("bill-1", fullText="Some bill text. " * 10)
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]

        from legislation_worker.tasks import vectorize_bills

        result = vectorize_bills.apply().get()

        assert result == {"total": 1, "vectorized": 1, "skipped_no_text": 0, "failed": 0}
        mock_chunk.assert_called_once_with(doc)
        mock_embed.assert_called_once_with(chunks)
        mock_upsert.assert_called_once_with("bill-1", chunks, embeddings)
        set_doc = mock_collection.update_one.call_args[0][1]["$set"]
        assert "vectorizedAt" in set_doc

    def test_no_chunks_counted_as_skipped(self, mock_collection, mocker):
        self._patch_vector_deps(mocker, chunks=[], embeddings=[])
        doc = make_bill("bill-1")
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]

        from legislation_worker.tasks import vectorize_bills

        result = vectorize_bills.apply().get()

        assert result["skipped_no_text"] == 1
        assert result["vectorized"] == 0
        set_doc = mock_collection.update_one.call_args[0][1]["$set"]
        assert "vectorizedAt" in set_doc

    def test_embed_error_counted_as_failed(self, mock_collection, mocker):
        mocker.patch("legislation_worker.vector_store.ensure_schema")
        mocker.patch("legislation_worker.chunker.chunk_bill", return_value=[MagicMock()])
        mocker.patch(
            "legislation_worker.vectorizer.embed_chunks",
            side_effect=RuntimeError("embed error"),
        )
        doc = make_bill("bill-1", fullText="text " * 50)
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [doc],
        ]

        from legislation_worker.tasks import vectorize_bills

        result = vectorize_bills.apply().get()

        assert result["failed"] == 1
        assert result["vectorized"] == 0
        mock_collection.update_one.assert_not_called()

    def test_bill_ids_filter_skips_vectorized_at_check(self, mock_collection, mocker):
        self._patch_vector_deps(mocker, chunks=[], embeddings=[])
        mock_collection.find.side_effect = [
            [{"id": "bill-1"}],
            [make_bill("bill-1")],
        ]

        from legislation_worker.tasks import vectorize_bills

        vectorize_bills.apply(kwargs={"bill_ids": ["bill-1"]}).get()

        first_query = mock_collection.find.call_args_list[0][0][0]
        assert "$in" in first_query.get("id", {})
        assert "vectorizedAt" not in first_query

    def test_empty_collection(self, mock_collection, mocker):
        mocker.patch("legislation_worker.vector_store.ensure_schema")
        mock_collection.find.return_value = iter([])

        from legislation_worker.tasks import vectorize_bills

        result = vectorize_bills.apply().get()

        assert result == {"total": 0, "vectorized": 0, "skipped_no_text": 0, "failed": 0}

    def test_second_bill_succeeds_after_first_fails(self, mock_collection, mocker):
        mocker.patch("legislation_worker.vector_store.ensure_schema")
        mocker.patch(
            "legislation_worker.chunker.chunk_bill",
            side_effect=[RuntimeError("fail"), [MagicMock()]],
        )
        mocker.patch("legislation_worker.vectorizer.embed_chunks", return_value=[[0.1] * 384])
        mocker.patch("legislation_worker.vector_store.upsert_bill_vectors", return_value=1)
        docs = [make_bill("bill-1"), make_bill("bill-2")]
        mock_collection.find.side_effect = [
            [{"id": d["id"]} for d in docs],
            docs,
        ]

        from legislation_worker.tasks import vectorize_bills

        result = vectorize_bills.apply().get()

        assert result["failed"] == 1
        assert result["vectorized"] == 1
