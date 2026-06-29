"""Phase 09: source-sync worker job dispatch (manual vs webhook) + idempotency."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from workers.source_sync_worker.worker import _process_job


def _fake_service(tmp_path):
    settings = SimpleNamespace(sync_job_state_path=tmp_path / "sync-job-state.json")
    return SimpleNamespace(
        _settings=settings,
        sync_all_training_files=MagicMock(),
        sync_events=MagicMock(),
    )


def test_manual_job_runs_full_sync(tmp_path):
    service = _fake_service(tmp_path)
    _process_job({"job_id": "j1", "job_type": "manual"}, service)

    service.sync_all_training_files.assert_called_once()
    service.sync_events.assert_not_called()
    assert "j1" in service._settings.sync_job_state_path.read_text(encoding="utf-8")


def test_webhook_job_builds_event_and_syncs(tmp_path):
    service = _fake_service(tmp_path)
    payload = {
        "job_id": "j2",
        "job_type": "webhook",
        "payload": {"path": "raw/sources/a.docx", "modified_at": "t", "entry_id": "e"},
    }
    _process_job(payload, service)

    service.sync_events.assert_called_once()
    (events,), _ = service.sync_events.call_args
    assert len(events) == 1
    event = events[0]
    assert event.path == "raw/sources/a.docx"
    assert event.event_type == "webhook"
    assert event.modified_at == "t"
    assert event.entry_id == "e"


def test_webhook_unsupported_extension_is_skipped_but_marked_processed(tmp_path):
    service = _fake_service(tmp_path)
    payload = {"job_id": "j3", "job_type": "webhook", "payload": {"path": "raw/sources/a.bin"}}
    _process_job(payload, service)

    service.sync_events.assert_not_called()
    assert "j3" in service._settings.sync_job_state_path.read_text(encoding="utf-8")


def test_webhook_missing_path_raises(tmp_path):
    service = _fake_service(tmp_path)
    with pytest.raises(ValueError, match="missing a path"):
        _process_job({"job_id": "j4", "job_type": "webhook", "payload": {}}, service)


def test_unknown_job_type_raises(tmp_path):
    service = _fake_service(tmp_path)
    with pytest.raises(ValueError, match="Unsupported source sync job type"):
        _process_job({"job_id": "j5", "job_type": "frobnicate"}, service)


def test_duplicate_job_id_is_idempotent(tmp_path):
    service = _fake_service(tmp_path)
    job = {"job_id": "dup", "job_type": "manual"}
    _process_job(job, service)
    _process_job(job, service)  # second time should be skipped

    assert service.sync_all_training_files.call_count == 1
