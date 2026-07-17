"""sync_events is fail-soft: one bad file cannot abort the whole sync, and the
per-file outcomes are surfaced in the SyncReport (and thus the sync report page)."""

from __future__ import annotations

from unittest.mock import MagicMock

from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.ingest_service import AutoIngestService, IngestFileResult
from packages.wiki_core.retrieval.index_service import IndexingReport
from tests.conftest import make_core_settings


def _service(tmp_path):
    """Real AutoIngestService with network/index collaborators stubbed out."""

    service = AutoIngestService(make_core_settings(tmp_path))
    service._source_sync = MagicMock()  # noqa: SLF001
    # download_file echoes a local path derived from the event path.
    service._source_sync.download_file.side_effect = lambda path: tmp_path / path  # noqa: SLF001
    service._indexer = MagicMock()  # noqa: SLF001
    service._indexer.upsert_modified_files.return_value = IndexingReport(
        mode="upsert", indexed_files=[], deleted_files=[], chunk_count=0
    )
    return service


def _event(path: str) -> SourceFileEvent:
    return SourceFileEvent(path=path, event_type="manual-sync", modified_at="2026-07-16T00:00:00Z", entry_id=path)


def test_one_failing_file_does_not_block_the_rest(tmp_path):
    service = _service(tmp_path)

    def ingest(local_path):
        if "bad" in str(local_path):
            raise ValueError("corrupt PDF")
        return IngestFileResult(updated_paths=["wiki/sources/ok.md"], empty=False)

    service._ingest_local_file = MagicMock(side_effect=ingest)  # noqa: SLF001

    report = service.sync_events(
        [_event("raw/sources/good1.pdf"), _event("raw/sources/bad.pdf"), _event("raw/sources/good2.pdf")]
    )

    # Both good files ingested; the bad one is recorded, not fatal.
    assert report.downloaded_files == ["raw/sources/good1.pdf", "raw/sources/good2.pdf"]
    assert len(report.failed_files) == 1
    assert report.failed_files[0]["path"] == "raw/sources/bad.pdf"
    assert "corrupt PDF" in report.failed_files[0]["error"]


def test_failed_file_is_not_recorded_in_state_and_retries(tmp_path):
    service = _service(tmp_path)
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        side_effect=ValueError("boom")
    )

    service.sync_events([_event("raw/sources/bad.pdf")])

    # State must not mark a failed file processed, so the next sync retries it.
    assert service._load_state() == {}  # noqa: SLF001


def test_successful_file_is_recorded_in_state(tmp_path):
    service = _service(tmp_path)
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        return_value=IngestFileResult(updated_paths=["wiki/sources/ok.md"], empty=False)
    )

    event = _event("raw/sources/ok.pdf")
    service.sync_events([event])

    state = service._load_state()  # noqa: SLF001
    assert state.get("raw/sources/ok.pdf") == service._event_key(event)  # noqa: SLF001


def test_empty_extraction_is_flagged_but_still_processed(tmp_path):
    service = _service(tmp_path)
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        return_value=IngestFileResult(updated_paths=[], empty=True)
    )

    event = _event("raw/sources/scan.pdf")
    report = service.sync_events([event])

    assert report.empty_extraction_files == ["raw/sources/scan.pdf"]
    # Empty extractions are marked processed (reprocessing yields nothing new).
    assert service._load_state().get("raw/sources/scan.pdf") == service._event_key(event)  # noqa: SLF001


def test_full_sync_counts_unsupported_and_publishes_report(tmp_path):
    service = _service(tmp_path)
    service._refresh_local_wiki_from_sharepoint = MagicMock()  # noqa: SLF001
    service._indexer.reconcile.return_value = IndexingReport(  # noqa: SLF001
        mode="upsert", indexed_files=[], deleted_files=[], chunk_count=0
    )
    service._source_sync.list_files_recursive.return_value = [  # noqa: SLF001
        _event("raw/sources/a.pdf"),
        _event("raw/sources/legacy.doc"),  # convertible -> ingestible
        _event("raw/sources/notes.md"),  # unsupported
        _event("raw/sources/clip.mov"),  # unsupported
    ]
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        return_value=IngestFileResult(updated_paths=["wiki/sources/a.md"], empty=False)
    )

    report = service.sync_all_training_files()

    # .md and .mov are unsupported; .pdf and .doc are ingestible.
    assert report.unsupported_files == {".md": 1, ".mov": 1}
    assert report.requested_files == 2

    # The report page was written locally and uploaded to SharePoint.
    report_page = service._settings.wiki_root / "reports" / "last-sync.md"  # noqa: SLF001
    assert report_page.exists()
    text = report_page.read_text(encoding="utf-8")
    assert "Unsupported file types" in text and "`.mov`: 1" in text
    service._source_sync.upload_text_file.assert_any_call(  # noqa: SLF001
        "wiki/reports/last-sync.md", text
    )


def test_report_page_is_excluded_from_indexing(tmp_path):
    from packages.wiki_core.content.file_page_store import FilePageStore

    settings = make_core_settings(tmp_path)
    (settings.wiki_root / "sources").mkdir(parents=True, exist_ok=True)
    (settings.wiki_root / "reports").mkdir(parents=True, exist_ok=True)
    (settings.wiki_root / "sources" / "a.md").write_text("# A", encoding="utf-8")
    (settings.wiki_root / "reports" / "last-sync.md").write_text("# Last sync report", encoding="utf-8")

    pages = {p.name for p in FilePageStore(settings).iter_wiki_pages()}
    assert "a.md" in pages
    assert "last-sync.md" not in pages


def test_publish_and_index_happen_before_state_is_saved(tmp_path):
    """If publish/index throws, state must not have been written (no
    processed-but-not-published files)."""

    service = _service(tmp_path)
    service._ingest_local_file = MagicMock(  # noqa: SLF001
        return_value=IngestFileResult(updated_paths=["wiki/sources/ok.md"], empty=False)
    )
    service._indexer.upsert_modified_files.side_effect = RuntimeError("index down")  # noqa: SLF001

    try:
        service.sync_events([_event("raw/sources/ok.pdf")])
    except RuntimeError:
        pass

    assert service._load_state() == {}  # noqa: SLF001
