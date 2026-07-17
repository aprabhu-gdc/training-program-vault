"""VaultAdminService: remove ordering, clean pruning, lint reporting (offline)."""

from __future__ import annotations

import json

import pytest

from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.progress import ProgressReporter
from packages.wiki_core.maintenance.vault_admin import VaultAdminService
from packages.wiki_core.retrieval.index_service import IndexingReport
from tests.conftest import make_core_settings


def _seed_core_pages(settings) -> None:
    wiki = settings.wiki_root
    (wiki / "sources").mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text(
        "---\ntitle: Index\ntype: index\n---\n\n## Sources\n\n- [[wiki/sources/foo|Foo]] - desc\n",
        encoding="utf-8",
    )
    (wiki / "log.md").write_text("---\ntitle: Log\ntype: log\n---\n\n", encoding="utf-8")


def test_remove_deletes_sharepoint_first_and_preserves_state(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _seed_core_pages(settings)
    (settings.wiki_root / "sources" / "foo.md").write_text(
        "---\ntitle: Foo\ntype: source\nsources:\n  - raw/sources/foo.pdf\n---\n\nbody\n", encoding="utf-8"
    )
    settings.source_sync_state_path.write_text(json.dumps({"raw/sources/foo.pdf": "fp"}), encoding="utf-8")

    svc = VaultAdminService(settings)
    order: list[str] = []
    monkeypatch.setattr(svc._source_sync, "delete_wiki_file", lambda rel: order.append("sharepoint_delete") or True)
    monkeypatch.setattr(svc._source_sync, "upload_text_file", lambda rel, content: order.append(f"upload:{rel}"))

    result = svc.remove_page("wiki/sources/foo.md", requested_by="Dana", progress=ProgressReporter())

    assert result["sharepoint_deleted"] is True
    assert order[0] == "sharepoint_delete"  # SharePoint deleted before any local publish
    assert not (settings.wiki_root / "sources" / "foo.md").exists()
    assert result["index_entry_removed"] is True
    assert "[[wiki/sources/foo" not in (settings.wiki_root / "index.md").read_text(encoding="utf-8")
    # /remove must NOT prune source-sync-state (that would re-ingest and resurrect).
    assert json.loads(settings.source_sync_state_path.read_text()) == {"raw/sources/foo.pdf": "fp"}


def test_remove_aborts_when_sharepoint_delete_fails(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _seed_core_pages(settings)
    (settings.wiki_root / "sources" / "foo.md").write_text(
        "---\ntitle: Foo\ntype: source\n---\n\nbody\n", encoding="utf-8"
    )
    svc = VaultAdminService(settings)

    def _boom(rel):
        raise RuntimeError("graph 500")

    monkeypatch.setattr(svc._source_sync, "delete_wiki_file", _boom)
    with pytest.raises(RuntimeError):
        svc.remove_page("wiki/sources/foo.md", requested_by="Dana", progress=ProgressReporter())

    # Local file is untouched because the SharePoint delete failed first.
    assert (settings.wiki_root / "sources" / "foo.md").exists()


def test_remove_rejects_protected_pages(tmp_path):
    settings = make_core_settings(tmp_path)
    _seed_core_pages(settings)
    svc = VaultAdminService(settings)
    with pytest.raises(ValueError):
        svc.remove_page("wiki/index.md", requested_by="Dana", progress=ProgressReporter())


def test_clean_reconciles_and_prunes_state_and_job_history(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    svc = VaultAdminService(settings)
    monkeypatch.setattr(
        svc._indexer,
        "reconcile",
        lambda: IndexingReport(mode="upsert", indexed_files=["wiki/a.md"], deleted_files=["wiki/gone.md"], chunk_count=2),
    )
    monkeypatch.setattr(
        svc._source_sync,
        "list_files_recursive",
        lambda root: [SourceFileEvent(path="raw/sources/foo.pdf", event_type="scan")],
    )
    settings.source_sync_state_path.write_text(
        json.dumps({"raw/sources/foo.pdf": "x", "raw/sources/bar.pdf": "y"}), encoding="utf-8"
    )
    settings.sync_job_state_path.write_text(
        json.dumps({"processed_job_ids": [str(i) for i in range(600)]}), encoding="utf-8"
    )

    result = svc.clean(progress=ProgressReporter())

    assert result["reindexed"] == 1 and result["index_deleted"] == 1
    assert result["state_pruned"] == 1  # bar.pdf no longer exists remotely
    assert json.loads(settings.source_sync_state_path.read_text()) == {"raw/sources/foo.pdf": "x"}
    assert result["job_ids_pruned"] == 100  # capped 600 -> 500


def test_clean_prunes_nothing_when_listing_fails(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    svc = VaultAdminService(settings)
    monkeypatch.setattr(
        svc._indexer, "reconcile", lambda: IndexingReport(mode="upsert", indexed_files=[], deleted_files=[], chunk_count=0)
    )

    def _boom(root):
        raise RuntimeError("graph down")

    monkeypatch.setattr(svc._source_sync, "list_files_recursive", _boom)
    settings.source_sync_state_path.write_text(json.dumps({"raw/sources/foo.pdf": "x"}), encoding="utf-8")

    result = svc.clean(progress=ProgressReporter())
    # Cannot confirm what's live -> prune nothing (never risk a needless re-ingest).
    assert result["state_pruned"] == 0
    assert json.loads(settings.source_sync_state_path.read_text()) == {"raw/sources/foo.pdf": "x"}


def test_lint_writes_report_with_deterministic_and_llm_findings(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _seed_core_pages(settings)
    (settings.wiki_root / "sources" / "a.md").write_text(
        "---\ntitle: A\ntype: source\n---\n\nbody a\n", encoding="utf-8"
    )
    svc = VaultAdminService(settings)
    monkeypatch.setattr(svc._source_sync, "upload_text_file", lambda rel, content: None)
    monkeypatch.setattr(
        svc._model_gateway,
        "complete_json",
        lambda **kw: {"findings": [{"type": "contradiction", "paths": ["wiki/sources/a.md"], "summary": "conflicts with X", "severity": "high"}]},
    )

    result = svc.lint(progress=ProgressReporter())

    assert result["pages_scanned"] >= 1
    assert result["findings_total"] >= 1
    report = settings.repo_root / "wiki" / "reports" / "last-lint.md"
    assert report.exists()
    assert "contradiction" in report.read_text(encoding="utf-8")


def test_lint_is_failsoft_when_llm_errors(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _seed_core_pages(settings)
    (settings.wiki_root / "sources" / "a.md").write_text(
        "---\ntitle: A\ntype: source\n---\n\nbody a\n", encoding="utf-8"
    )
    svc = VaultAdminService(settings)
    monkeypatch.setattr(svc._source_sync, "upload_text_file", lambda rel, content: None)

    def _boom(**kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(svc._model_gateway, "complete_json", _boom)

    # Must not raise; deterministic findings still produced (a.md is an orphan).
    result = svc.lint(progress=ProgressReporter())
    assert result["findings_total"] >= 1
