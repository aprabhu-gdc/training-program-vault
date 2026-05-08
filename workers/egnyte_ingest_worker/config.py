"""Configuration for the Egnyte ingest worker."""

from __future__ import annotations

from apps.ingest_api.config import IngestQueueSettings


WorkerSettings = IngestQueueSettings


__all__ = ["WorkerSettings"]
