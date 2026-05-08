"""Background worker that consumes queued ingest jobs."""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.shared.messaging.service_bus import process_queue_messages
from packages.wiki_core.ingest.ingest_service import AutoIngestService

from .config import WorkerSettings


LOGGER = logging.getLogger(__name__)


def _process_job(payload: dict[str, Any], service: AutoIngestService) -> None:
    job_type = str(payload.get("job_type") or "")
    job_id = str(payload.get("job_id") or "unknown")
    LOGGER.info("Processing ingest job job_id=%s job_type=%s", job_id, job_type)

    if job_type == "manual":
        service.sync_all_training_files()
        return

    if job_type == "webhook":
        raw_payload = payload.get("payload")
        if not isinstance(raw_payload, dict):
            raise ValueError("Webhook ingest job requires a JSON object payload.")
        service.sync_from_webhook(raw_payload)
        return

    raise ValueError(f"Unsupported ingest job type: {job_type}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = WorkerSettings.from_env()
    settings.validate_queue()
    service = AutoIngestService(settings.backend)

    while True:
        processed = process_queue_messages(
            connection_string=settings.service_bus_connection_string,
            fully_qualified_namespace=settings.service_bus_namespace,
            queue_name=settings.service_bus_queue_name,
            processor=lambda payload: _process_job(payload, service),
            max_message_count=1,
            max_wait_time=5,
        )
        if processed == 0:
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
