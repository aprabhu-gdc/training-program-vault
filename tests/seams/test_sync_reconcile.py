"""sync_all_training_files reconciles the index after the per-file upserts."""

from __future__ import annotations

from unittest.mock import MagicMock

from packages.wiki_core.ingest.ingest_service import AutoIngestService, SyncReport
from packages.wiki_core.retrieval.index_service import IndexingReport
from tests.conftest import make_core_settings


def _service(tmp_path):
    """A real AutoIngestService with network-touching collaborators stubbed out."""

    service = AutoIngestService(make_core_settings(tmp_path))
    service._refresh_local_wiki_from_sharepoint = MagicMock()  # noqa: SLF001
    service._source_sync = MagicMock()  # noqa: SLF001
    service._source_sync.list_files_recursive.return_value = []
    empty_index = IndexingReport(mode="upsert", indexed_files=[], deleted_files=[], chunk_count=0)
    service.sync_events = MagicMock(  # noqa: SLF001
        return_value=SyncReport(
            requested_files=0,
            downloaded_files=[],
            updated_wiki_files=[],
            skipped_files=[],
            index_report=empty_index,
        )
    )
    service._indexer = MagicMock()  # noqa: SLF001
    service._indexer.reconcile.return_value = empty_index
    return service


def test_full_sync_reconciles_after_upserts(tmp_path):
    service = _service(tmp_path)
    service.sync_all_training_files()

    service.sync_events.assert_called_once()
    service._indexer.reconcile.assert_called_once()  # noqa: SLF001


def test_reconcile_failure_does_not_break_the_sync(tmp_path):
    service = _service(tmp_path)
    service._indexer.reconcile.side_effect = RuntimeError("lancedb hiccup")  # noqa: SLF001

    # Must return the sync report despite the reconcile blowing up.
    report = service.sync_all_training_files()
    assert report.requested_files == 0
    service._indexer.reconcile.assert_called_once()  # noqa: SLF001
