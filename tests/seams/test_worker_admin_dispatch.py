"""Worker dispatch of admin jobs (remove/clean/lint) and cooperative cancel."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from packages.wiki_core.ingest.progress import (
    SyncCancelledError,
    read_progress,
    write_cancel,
)
from workers.source_sync_worker import worker as worker_mod
from workers.source_sync_worker.worker import _process_job, _run_reconcile


def _fake_service(tmp_path):
    settings = SimpleNamespace(
        sync_job_state_path=tmp_path / "sync-job-state.json",
        sync_progress_path=tmp_path / "sync-progress.json",
        sync_cancel_path=tmp_path / "sync-cancel.json",
        admin_job_progress_path=tmp_path / "admin-job-progress.json",
    )
    return SimpleNamespace(
        _settings=settings,
        sync_all_training_files=MagicMock(),
        sync_events=MagicMock(),
    )


# --- admin jobs ----------------------------------------------------------------

def test_lint_job_dispatches_to_admin_service(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    admin = SimpleNamespace(remove_page=MagicMock(), clean=MagicMock(), lint=MagicMock())
    monkeypatch.setattr(worker_mod, "_get_admin_service", lambda svc: admin)

    _process_job({"job_id": "L1", "job_type": "lint"}, service)

    admin.lint.assert_called_once()
    assert "L1" in service._settings.sync_job_state_path.read_text(encoding="utf-8")


def test_remove_job_passes_path_and_requester(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    admin = SimpleNamespace(remove_page=MagicMock(), clean=MagicMock(), lint=MagicMock())
    monkeypatch.setattr(worker_mod, "_get_admin_service", lambda svc: admin)

    _process_job(
        {"job_id": "R1", "job_type": "remove", "payload": {"path": "wiki/sources/foo.md"},
         "requested_by_user_name": "Dana"},
        service,
    )

    (_, kwargs) = admin.remove_page.call_args
    args = admin.remove_page.call_args.args
    assert args[0] == "wiki/sources/foo.md"
    assert kwargs["requested_by"] == "Dana"


def test_admin_job_is_idempotent_on_redelivery(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    admin = SimpleNamespace(remove_page=MagicMock(), clean=MagicMock(), lint=MagicMock())
    monkeypatch.setattr(worker_mod, "_get_admin_service", lambda svc: admin)
    job = {"job_id": "C1", "job_type": "clean"}
    _process_job(job, service)
    _process_job(job, service)
    assert admin.clean.call_count == 1


# --- stopsync (cooperative cancel) ---------------------------------------------

def test_manual_job_cancelled_before_start(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    write_cancel(service._settings.sync_cancel_path, job_id="M1", requested_by_user_name="Dana")

    _process_job({"job_id": "M1", "job_type": "manual"}, service)

    service.sync_all_training_files.assert_not_called()
    record = read_progress(service._settings.sync_progress_path)
    assert record["status"] == "cancelled"
    # Sentinel cleared and job marked processed (message would complete).
    assert not service._settings.sync_cancel_path.exists()
    assert "M1" in service._settings.sync_job_state_path.read_text(encoding="utf-8")


def test_manual_job_cancelled_mid_run_returns_normally(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    service.sync_all_training_files.side_effect = SyncCancelledError("stop after file 3")

    # Must NOT raise (raising would abandon the Service Bus message and redeliver).
    _process_job({"job_id": "M2", "job_type": "manual"}, service)

    record = read_progress(service._settings.sync_progress_path)
    assert record["status"] == "cancelled"
    assert "M2" in service._settings.sync_job_state_path.read_text(encoding="utf-8")


def test_stale_foreign_sentinel_does_not_cancel_new_job(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    # A leftover sentinel from an old job must not kill this one.
    write_cancel(service._settings.sync_cancel_path, job_id="OLD", requested_by_user_name=None)

    _process_job({"job_id": "NEW", "job_type": "manual"}, service)

    service.sync_all_training_files.assert_called_once()
    record = read_progress(service._settings.sync_progress_path)
    assert record["status"] == "completed"
    assert not service._settings.sync_cancel_path.exists()


def test_reconcile_honours_cancellation(tmp_path, monkeypatch):
    service = _fake_service(tmp_path)
    service.sync_all_training_files.side_effect = SyncCancelledError("cancelled")
    _run_reconcile(service)  # must not raise
    record = read_progress(service._settings.sync_progress_path)
    assert record["status"] == "cancelled"
