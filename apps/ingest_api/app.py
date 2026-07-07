"""Aiohttp ingest API that queues SharePoint sync jobs (manual + webhook)."""

from __future__ import annotations

import json
import logging
import uuid

from aiohttp import web

from packages.contracts.sync import SourceFileEvent, SyncJobAccepted, SyncJobMessage
from packages.shared.documents.extract_text import SUPPORTED_EXTENSIONS
from packages.shared.messaging.service_bus import send_json_message
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.settings import CoreSettings

from .config import IngestQueueSettings
from packages.shared.logging import configure_logging


configure_logging()
LOGGER = logging.getLogger(__name__)


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

    async def manual_sync(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Only application/json payloads are supported."}, status=415)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Malformed JSON payload."}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Sync payload must be a JSON object."}, status=400)

        accepted = _queue_job(
            settings,
            SyncJobMessage(
                job_id=uuid.uuid4().hex,
                job_type="manual",
                payload=None,
                requested_by_user_id=(str(body.get("requested_by_user_id")) if body.get("requested_by_user_id") is not None else None),
                requested_by_user_name=(str(body.get("requested_by_user_name")) if body.get("requested_by_user_name") is not None else None),
                source="teams-manual-sync",
            ),
        )
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

    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_post("/admin/sync", manual_sync)
    app.router.add_post("/api/webhooks/sharepoint", sharepoint_webhook)
    app["settings"] = settings
    return app


if __name__ == "__main__":
    application = create_app()
    settings: IngestQueueSettings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
