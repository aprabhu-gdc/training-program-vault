"""Aiohttp ingest API that queues SharePoint sync jobs (manual + webhook)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from aiohttp import web

from packages.contracts.sync import SourceFileEvent, SyncJobAccepted, SyncJobMessage
from packages.shared.documents.extract_text import SUPPORTED_EXTENSIONS
from packages.shared.messaging.service_bus import send_json_message
from packages.wiki_core.ingest.progress import (
    TERMINAL_STATUSES,
    cancel_requested_for,
    is_stale,
    read_progress,
    write_cancel,
    write_cancelled_from,
    write_queued,
)
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.ingest.subscription_manager import SubscriptionManager
from packages.wiki_core.settings import CoreSettings

from .config import IngestQueueSettings
from packages.shared.logging import configure_logging


configure_logging()
LOGGER = logging.getLogger(__name__)

# How often the webhook subscription is checked (created/renewed as needed).
SUBSCRIPTION_CHECK_INTERVAL_SECONDS = 3600.0


def _queue_job(settings: IngestQueueSettings, job: SyncJobMessage) -> SyncJobAccepted:
    send_json_message(
        connection_string=settings.service_bus_connection_string,
        fully_qualified_namespace=settings.service_bus_namespace,
        queue_name=settings.service_bus_queue_name,
        payload={
            "job_id": job.job_id,
            "job_type": job.job_type,
            "payload": job.payload,
            "requested_by_user_id": job.requested_by_user_id,
            "requested_by_user_name": job.requested_by_user_name,
            "source": job.source,
        },
        message_id=job.job_id,
    )
    return SyncJobAccepted(job_id=job.job_id, status="accepted")


def _event_in_scope(adapter: SharePointSourceSyncAdapter, event: SourceFileEvent) -> bool:
    if not adapter.is_in_scope(event):
        return False
    suffix = ""
    if "." in event.path:
        suffix = "." + event.path.rsplit(".", maxsplit=1)[1].lower()
    return suffix in SUPPORTED_EXTENSIONS


def create_app() -> web.Application:
    settings = IngestQueueSettings.from_env()
    settings.validate_queue()

    core_settings = CoreSettings.from_env()
    adapter: SharePointSourceSyncAdapter | None = None

    def _get_adapter() -> SharePointSourceSyncAdapter:
        nonlocal adapter
        if adapter is None:
            adapter = SharePointSourceSyncAdapter(core_settings)
        return adapter

    async def healthcheck(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def sync_status(_: web.Request) -> web.Response:
        record = read_progress(core_settings.sync_progress_path)
        if record is None:
            return web.json_response({"status": "none"})
        # Surface a pending cancel immediately (before the worker's next heartbeat)
        # so the progress card can show "Stopping…" within one poll tick. Never
        # persisted — added only to the served copy to avoid a write race.
        if record.get("status") not in TERMINAL_STATUSES and cancel_requested_for(
            core_settings.sync_cancel_path, str(record.get("job_id") or "")
        ):
            record = {**record, "cancel_requested": True}
        return web.json_response(record)

    async def cancel_sync(request: web.Request) -> web.Response:
        body: dict = {}
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except (json.JSONDecodeError, ValueError):
            body = {}
        requested_by_user_name = (
            str(body.get("requested_by_user_name")) if body.get("requested_by_user_name") is not None else None
        )

        record = read_progress(core_settings.sync_progress_path)
        if record is None or record.get("status") in TERMINAL_STATUSES:
            return web.json_response({"error": "No sync is currently queued or running."}, status=404)

        job_id = str(record.get("job_id") or "")

        if is_stale(record):
            # The worker stopped heart-beating and will never write a terminal
            # record, so correct the status in place to keep the card truthful.
            # Also drop a sentinel in case the worker is alive-but-slow.
            updated = write_cancelled_from(
                core_settings.sync_progress_path,
                record,
                error="Cancelled while stalled (no worker heartbeat).",
            )
            if job_id:
                write_cancel(
                    core_settings.sync_cancel_path,
                    job_id=job_id,
                    requested_by_user_name=requested_by_user_name,
                )
            return web.json_response(
                {"job_id": job_id, "status": "cancelled_stale", "progress": updated}, status=200
            )

        # Active and healthy: request a cooperative stop; the worker finishes the
        # in-flight file, writes a terminal "cancelled" record, and completes the
        # queue message. Idempotent — a repeat request just rewrites the sentinel.
        write_cancel(
            core_settings.sync_cancel_path,
            job_id=job_id,
            requested_by_user_name=requested_by_user_name,
        )
        return web.json_response(
            {"job_id": job_id, "status": "cancel_requested", "progress": record}, status=202
        )

    async def manual_sync(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Only application/json payloads are supported."}, status=415)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Malformed JSON payload."}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Sync payload must be a JSON object."}, status=400)

        # The vault index is global: one sync serves everyone. If a fresh job is
        # already queued/running, return it (409) instead of enqueuing a duplicate
        # so a second user's /sync attaches to the in-flight job's live status.
        current = read_progress(core_settings.sync_progress_path)
        if current and current.get("status") in {"queued", "running"} and not is_stale(current):
            return web.json_response({"status": "already_running", "progress": current}, status=409)

        requested_by_user_name = (
            str(body.get("requested_by_user_name")) if body.get("requested_by_user_name") is not None else None
        )
        accepted = _queue_job(
            settings,
            SyncJobMessage(
                job_id=uuid.uuid4().hex,
                job_type="manual",
                payload=None,
                requested_by_user_id=(str(body.get("requested_by_user_id")) if body.get("requested_by_user_id") is not None else None),
                requested_by_user_name=requested_by_user_name,
                source="teams-manual-sync",
            ),
        )
        # Best-effort initial record so a status poll right after enqueue shows
        # "queued" before the worker picks the job up. Never block the enqueue.
        try:
            write_queued(
                core_settings.sync_progress_path,
                job_id=accepted.job_id,
                job_type="manual",
                requested_by_user_name=requested_by_user_name,
            )
        except OSError:
            LOGGER.warning("Failed to write initial queued sync-progress record", exc_info=True)
        return web.json_response({"job_id": accepted.job_id, "status": accepted.status}, status=202)

    async def admin_job_status(_: web.Request) -> web.Response:
        record = read_progress(core_settings.admin_job_progress_path)
        if record is None:
            return web.json_response({"status": "none"})
        return web.json_response(record)

    async def submit_admin_job(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Only application/json payloads are supported."}, status=415)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Malformed JSON payload."}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Job payload must be a JSON object."}, status=400)

        job_type = str(body.get("job_type") or "").strip()
        if job_type not in {"remove", "clean", "lint"}:
            return web.json_response({"error": f"Unsupported admin job type: {job_type!r}"}, status=400)

        # One admin job at a time (same gate shape as manual_sync).
        current = read_progress(core_settings.admin_job_progress_path)
        if current and current.get("status") in {"queued", "running"} and not is_stale(current):
            return web.json_response({"status": "already_running", "progress": current}, status=409)

        # Mutating jobs must not silently queue behind an hours-long sync; tell the
        # caller instead so they can retry after it finishes.
        if job_type in {"remove", "clean"}:
            sync = read_progress(core_settings.sync_progress_path)
            if sync and sync.get("status") == "running" and not is_stale(sync):
                return web.json_response({"status": "sync_running", "progress": sync}, status=409)

        requested_by_user_name = (
            str(body.get("requested_by_user_name")) if body.get("requested_by_user_name") is not None else None
        )
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        accepted = _queue_job(
            settings,
            SyncJobMessage(
                job_id=uuid.uuid4().hex,
                job_type=job_type,
                payload=payload,
                requested_by_user_id=(str(body.get("requested_by_user_id")) if body.get("requested_by_user_id") is not None else None),
                requested_by_user_name=requested_by_user_name,
                source=f"teams-admin-{job_type}",
            ),
        )
        try:
            write_queued(
                core_settings.admin_job_progress_path,
                job_id=accepted.job_id,
                job_type=job_type,
                requested_by_user_name=requested_by_user_name,
            )
        except OSError:
            LOGGER.warning("Failed to write initial queued admin-job record", exc_info=True)
        return web.json_response({"job_id": accepted.job_id, "status": accepted.status}, status=202)

    async def sharepoint_webhook(request: web.Request) -> web.Response:
        # Microsoft Graph subscription validation handshake. Graph POSTs with a
        # validationToken query string and expects a plaintext echo within 10s.
        validation_token = request.query.get("validationToken")
        if validation_token is not None:
            return web.Response(text=validation_token, content_type="text/plain", status=200)

        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Malformed JSON payload."}, status=400)

        events = _get_adapter().parse_webhook_payload(payload)
        queued = 0
        for event in events:
            if not _event_in_scope(_get_adapter(), event):
                continue
            _queue_job(
                settings,
                SyncJobMessage(
                    job_id=uuid.uuid4().hex,
                    job_type="webhook",
                    payload={
                        "path": event.path,
                        "modified_at": event.modified_at,
                        "entry_id": event.entry_id,
                    },
                    source="sharepoint-webhook",
                ),
            )
            queued += 1

        LOGGER.info("SharePoint webhook accepted notifications=%s queued=%s", len(events), queued)
        # Graph requires a 2xx within 10 seconds; the actual ingest happens in the worker.
        return web.Response(status=202)

    subscription_manager = SubscriptionManager(_get_adapter, core_settings)

    async def _subscription_loop(app: web.Application):
        """Background task keeping the Graph webhook subscription alive."""

        async def _cycle() -> None:
            while True:
                # ensure_once is sync (httpx) and never raises; run it off-loop.
                await asyncio.to_thread(subscription_manager.ensure_once)
                await asyncio.sleep(SUBSCRIPTION_CHECK_INTERVAL_SECONDS)

        task = asyncio.create_task(_cycle())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_post("/admin/sync", manual_sync)
    app.router.add_get("/admin/sync/status", sync_status)
    app.router.add_post("/admin/sync/cancel", cancel_sync)
    app.router.add_post("/admin/jobs", submit_admin_job)
    app.router.add_get("/admin/jobs/status", admin_job_status)
    app.router.add_post("/api/webhooks/sharepoint", sharepoint_webhook)
    app.cleanup_ctx.append(_subscription_loop)
    app["settings"] = settings
    app["subscription_manager"] = subscription_manager
    return app


if __name__ == "__main__":
    application = create_app()
    settings: IngestQueueSettings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
