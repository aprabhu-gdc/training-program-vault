"""Cooperative cancel in the ingest sync loop: stop after the current file, with
already-processed files left durable and consistent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.ingest_service import AutoIngestService, IngestFileResult
from packages.wiki_core.ingest.progress import ProgressReporter, SyncCancelledError
from packages.wiki_core.retrieval.index_service import IndexingReport
from tests.conftest import make_core_settings


def _service(tmp_path):
    service = AutoIngestService(make_core_settings(tmp_path))
    service._source_sync = MagicMock()  # noqa: SLF001
    service._source_sync.download_file.side_effect = lambda path: tmp_path / path  # noqa: SLF001
    service._indexer = MagicMock()  # noqa: SLF001
    service._indexer.upsert_modified_files.return_value = IndexingReport(
        mode="upsert", indexed_files=[], deleted_files=[], chunk_count=0
    )
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        return_value=IngestFileResult(updated_paths=["wiki/sources/ok.md"], empty=False)
    )
    return service


def _event(path: str) -> SourceFileEvent:
    return SourceFileEvent(path=path, event_type="manual-sync", modified_at="t", entry_id=path)


class _CancelAfter(ProgressReporter):
    """Requests cancel once `after` files have been recorded as processed."""

    def __init__(self, after: int) -> None:
        self.after = after
        self.done = 0

    def record(self, outcome, *, path=None, error=None) -> None:
        self.done += 1

    def should_cancel(self) -> bool:
        return self.done >= self.after


def test_cancel_after_current_file_persists_processed_and_skips_rest(tmp_path):
    service = _service(tmp_path)
    events = [_event(f"raw/sources/{name}.pdf") for name in ("a", "b", "c")]

    with pytest.raises(SyncCancelledError):
        service.sync_events(events, progress=_CancelAfter(after=2))

    # Files a and b were processed and fingerprinted (skip next time); c was not.
    state = service._load_state()  # noqa: SLF001
    assert set(state) == {"raw/sources/a.pdf", "raw/sources/b.pdf"}
    assert "raw/sources/c.pdf" not in state
    # The already-processed work was published + indexed before we raised.
    service._indexer.upsert_modified_files.assert_called_once()  # noqa: SLF001


def test_no_cancel_completes_normally(tmp_path):
    service = _service(tmp_path)
    report = service.sync_events([_event("raw/sources/a.pdf")], progress=ProgressReporter())
    assert report.downloaded_files == ["raw/sources/a.pdf"]
