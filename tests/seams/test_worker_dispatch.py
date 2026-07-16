"""Phase 09: source-sync worker job dispatch (manual vs webhook) + idempotency."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from workers.source_sync_worker import worker as worker_mod
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


def test_reconcile_runs_full_sync(tmp_path):
    service = _fake_service(tmp_path)
    worker_mod._run_reconcile(service)
    service.sync_all_training_files.assert_called_once()


def test_reconcile_swallows_sync_failure(tmp_path):
    service = _fake_service(tmp_path)
    service.sync_all_training_files.side_effect = RuntimeError("sharepoint down")
    worker_mod._run_reconcile(service)  # must not raise
    service.sync_all_training_files.assert_called_once()


def test_duplicate_job_id_is_idempotent(tmp_path):
    service = _fake_service(tmp_path)
    job = {"job_id": "dup", "job_type": "manual"}
    _process_job(job, service)
    _process_job(job, service)  # second time should be skipped

    assert service.sync_all_training_files.call_count == 1


def _queue_settings():
    return SimpleNamespace(
        service_bus_connection_string="conn",
        service_bus_namespace="",
        service_bus_queue_name="q",
    )


def test_poll_once_swallows_transient_errors(monkeypatch):
    # A transient AMQP/Service Bus error must not escape (it previously killed
    # the worker, which nothing restarts).
    def boom(**kwargs):
        raise RuntimeError("AMQPLinkError: Link detached unexpectedly")

    monkeypatch.setattr(worker_mod, "process_queue_messages", boom)
    assert worker_mod._poll_once(_queue_settings(), object()) == 0


def test_poll_once_returns_processed_count(monkeypatch):
    monkeypatch.setattr(worker_mod, "process_queue_messages", lambda **kwargs: 3)
    assert worker_mod._poll_once(_queue_settings(), object()) == 3
