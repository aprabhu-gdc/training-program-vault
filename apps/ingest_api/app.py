"""Aiohttp ingest API that queues Egnyte and manual sync jobs."""

from __future__ import annotations

import json
import logging
import os
import uuid

from aiohttp import web

from packages.contracts.sync import SyncJobAccepted, SyncJobMessage
from packages.shared.messaging.service_bus import send_json_message

from .config import IngestQueueSettings


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
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


def create_app() -> web.Application:
    settings = IngestQueueSettings.from_env()
    settings.validate_queue()

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

    async def egnyte_webhook(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Only application/json payloads are supported."}, status=415)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            LOGGER.warning("Rejected malformed JSON request on /api/webhooks/egnyte")
            return web.json_response({"error": "Malformed JSON payload."}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Webhook payload must be a JSON object."}, status=400)

        accepted = _queue_job(
            settings,
            SyncJobMessage(
                job_id=uuid.uuid4().hex,
                job_type="webhook",
                payload=payload,
                source="egnyte-webhook",
            ),
        )
        return web.json_response({"job_id": accepted.job_id, "status": accepted.status}, status=202)

    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_post("/admin/sync", manual_sync)
    app.router.add_post("/api/webhooks/egnyte", egnyte_webhook)
    app["settings"] = settings
    return app


if __name__ == "__main__":
    application = create_app()
    settings: IngestQueueSettings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
