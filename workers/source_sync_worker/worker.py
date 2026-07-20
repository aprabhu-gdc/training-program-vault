"""Background worker that consumes queued source sync jobs."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from packages.contracts.sync import SourceFileEvent
from packages.shared.documents.extract_text import CONVERTIBLE_EXTENSIONS, SUPPORTED_EXTENSIONS
from packages.shared.logging import configure_logging
from packages.shared.messaging.service_bus import process_queue_messages
from packages.wiki_core.ingest.ingest_service import AutoIngestService
from packages.wiki_core.ingest.progress import (
    FileProgressReporter,
    SyncCancelledError,
    cancel_requested_for,
    clear_cancel,
    read_cancel,
)
from packages.wiki_core.maintenance.vault_admin import VaultAdminService

from .config import WorkerSettings


LOGGER = logging.getLogger(__name__)

_ADMIN_SERVICE: VaultAdminService | None = None


def _get_admin_service(service: AutoIngestService) -> VaultAdminService:
    """Lazily build (and cache) the maintenance service, like AutoIngestService."""
    global _ADMIN_SERVICE
    if _ADMIN_SERVICE is None:
        _ADMIN_SERVICE = VaultAdminService(service._settings)
    return _ADMIN_SERVICE


def _clear_stale_foreign_cancel(cancel_path, job_id: str) -> None:
    """Drop a cancel sentinel left over from a *different* job.

    A stale sentinel must never cancel a later sync it was not meant for.
    """
    record = read_cancel(cancel_path)
    if record and record.get("job_id") != job_id:
        LOGGER.info("Clearing stale cancel sentinel for old job_id=%s", record.get("job_id"))
        clear_cancel(cancel_path)


def _load_processed_jobs(service: AutoIngestService) -> set[str]:
    path = service._settings.sync_job_state_path
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    processed = payload.get("processed_job_ids")
    if not isinstance(processed, list):
        return set()
    return {str(job_id) for job_id in processed if str(job_id).strip()}


def _save_processed_jobs(service: AutoIngestService, processed_job_ids: set[str]) -> None:
    path = service._settings.sync_job_state_path
    payload = {"processed_job_ids": sorted(processed_job_ids)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _process_job(payload: dict[str, Any], service: AutoIngestService) -> None:
    job_type = str(payload.get("job_type") or "")
    job_id = str(payload.get("job_id") or "unknown")
    processed_job_ids = _load_processed_jobs(service)

    if job_id in processed_job_ids:
        LOGGER.info("Skipping already processed source sync job job_id=%s job_type=%s", job_id, job_type)
        return

    LOGGER.info("Processing source sync job job_id=%s job_type=%s", job_id, job_type)

    if job_type == "manual":
        cancel_path = service._settings.sync_cancel_path
        requested_by = (
            str(payload.get("requested_by_user_name")) if payload.get("requested_by_user_name") else None
        )
        reporter = FileProgressReporter(
            service._settings.sync_progress_path,
            job_id=job_id,
            job_type="manual",
            requested_by_user_name=requested_by,
            cancel_path=cancel_path,
        )

        # Cancel-before-start: a stop requested while the job was still queued is
        # honoured without ever starting the (hours-long) sync.
        if cancel_requested_for(cancel_path, job_id):
            LOGGER.info("Manual sync job_id=%s cancelled before start", job_id)
            reporter.finish_cancelled()
            clear_cancel(cancel_path)
            processed_job_ids.add(job_id)
            _save_processed_jobs(service, processed_job_ids)
            return

        _clear_stale_foreign_cancel(cancel_path, job_id)
        reporter.start()
        try:
            service.sync_all_training_files(progress=reporter)
        except SyncCancelledError:
            # Clean cooperative stop: return normally so the Service Bus message is
            # COMPLETED (raising would abandon it and redeliver, restarting the sync).
            LOGGER.info("Manual sync job_id=%s cancelled mid-run", job_id)
            reporter.finish_cancelled()
            clear_cancel(cancel_path)
            processed_job_ids.add(job_id)
            _save_processed_jobs(service, processed_job_ids)
            return
        except Exception as exc:
            reporter.finish_error(f"{type(exc).__name__}: {exc}")
            raise
        reporter.finish_ok()
        clear_cancel(cancel_path)  # clear any late sentinel that raced completion
        processed_job_ids.add(job_id)
        _save_processed_jobs(service, processed_job_ids)
        return

    if job_type == "webhook":
        job_payload = payload.get("payload") or {}
        if not isinstance(job_payload, dict):
            raise ValueError(f"Webhook job {job_id} has malformed payload: {job_payload!r}")
        path = str(job_payload.get("path") or "").strip()
        if not path:
            raise ValueError(f"Webhook job {job_id} is missing a path.")
        suffix = ""
        if "." in path:
            suffix = "." + path.rsplit(".", maxsplit=1)[1].lower()
        if suffix not in SUPPORTED_EXTENSIONS and suffix not in CONVERTIBLE_EXTENSIONS:
            LOGGER.info("Skipping webhook job for unsupported extension job_id=%s path=%s", job_id, path)
            processed_job_ids.add(job_id)
            _save_processed_jobs(service, processed_job_ids)
            return

        event = SourceFileEvent(
            path=path,
            event_type="webhook",
            modified_at=(str(job_payload.get("modified_at")) if job_payload.get("modified_at") else None),
            entry_id=(str(job_payload.get("entry_id")) if job_payload.get("entry_id") else None),
        )
        service.sync_events([event])
        processed_job_ids.add(job_id)
        _save_processed_jobs(service, processed_job_ids)
        return

    if job_type in {"remove", "clean", "lint"}:
        requested_by = (
            str(payload.get("requested_by_user_name")) if payload.get("requested_by_user_name") else None
        )
        reporter = FileProgressReporter(
            service._settings.admin_job_progress_path,
            job_id=job_id,
            job_type=job_type,
            requested_by_user_name=requested_by,
        )
        reporter.start()
        admin = _get_admin_service(service)
        job_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        try:
            if job_type == "remove":
                admin.remove_page(str(job_payload.get("path") or ""), requested_by=requested_by, progress=reporter)
            elif job_type == "clean":
                admin.clean(progress=reporter)
            else:
                admin.lint(progress=reporter)
        except Exception as exc:
            # The service calls finish_ok() itself on success; on failure record it here.
            reporter.finish_error(f"{type(exc).__name__}: {exc}")
            raise
        processed_job_ids.add(job_id)
        _save_processed_jobs(service, processed_job_ids)
        return

    raise ValueError(f"Unsupported source sync job type: {job_type}")


def _poll_once(settings: WorkerSettings, service: AutoIngestService) -> int:
    """Run one receive/process cycle. Never raises.

    Transient Service Bus / AMQP conditions (e.g. ``AMQPLinkError: Link detached
    unexpectedly``) surface from ``receive_messages`` outside the per-message
    try/except and would otherwise propagate out of the worker loop and kill the
    process. Nothing restarts a crashed background worker, so we log and swallow
    here and let the next cycle reconnect with a fresh client.
    """

    try:
        return process_queue_messages(
            connection_string=settings.service_bus_connection_string,
            fully_qualified_namespace=settings.service_bus_namespace,
            queue_name=settings.service_bus_queue_name,
            processor=lambda payload: _process_job(payload, service),
            max_message_count=1,
            max_wait_time=5,
            # A full manual sync of the whole vault runs an LLM call per file and
            # can take hours; keep the message lock alive well past the 1h default
            # so a long-but-healthy sync isn't abandoned and redelivered mid-run.
            max_lock_renewal_duration=6 * 3600,
            treat_completion_lock_loss_as_processed=True,
        )
    except Exception:
        LOGGER.exception("Source sync poll cycle failed; retrying after backoff")
        return 0


def _run_reconcile(service: AutoIngestService) -> None:
    """Periodic full sync: heals content missed by webhook notifications.

    Cheap in the common case — unchanged files are skipped via the per-file
    fingerprint state. Never raises; a failed sweep retries next interval.
    """

    LOGGER.info("Running scheduled reconciliation sync")
    cancel_path = service._settings.sync_cancel_path
    job_id = uuid.uuid4().hex
    reporter = FileProgressReporter(
        service._settings.sync_progress_path,
        job_id=job_id,
        job_type="scheduled",
        cancel_path=cancel_path,
    )
    _clear_stale_foreign_cancel(cancel_path, job_id)
    reporter.start()
    try:
        service.sync_all_training_files(progress=reporter)
    except SyncCancelledError:
        LOGGER.info("Scheduled reconciliation sync cancelled")
        reporter.finish_cancelled()
        clear_cancel(cancel_path)
        return
    except Exception as exc:
        reporter.finish_error(f"{type(exc).__name__}: {exc}")
        LOGGER.exception("Reconciliation sync failed; retrying next interval")
        return
    reporter.finish_ok()
    clear_cancel(cancel_path)


def main() -> int:
    configure_logging()
    settings = WorkerSettings.from_env()
    settings.validate_queue()
    service = AutoIngestService(settings.backend)

    reconcile_seconds = max(settings.reconcile_hours, 0.0) * 3600.0
    next_reconcile = time.monotonic() + reconcile_seconds if reconcile_seconds else None

    while True:
        if _poll_once(settings, service) == 0:
            time.sleep(2)
        if next_reconcile is not None and time.monotonic() >= next_reconcile:
            _run_reconcile(service)
            next_reconcile = time.monotonic() + reconcile_seconds


if __name__ == "__main__":
    raise SystemExit(main())
