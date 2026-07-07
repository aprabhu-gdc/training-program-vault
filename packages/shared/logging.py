"""Shared process-wide logging configuration for all runtime entrypoints.

Every long-running process (the Teams bot, the ingest API, and the sync worker)
should call :func:`configure_logging` exactly once at startup instead of calling
``logging.basicConfig`` directly, so log level and Azure SDK noise suppression stay
consistent across processes.
"""

from __future__ import annotations

import logging
import os


# The Azure SDKs emit very high-volume INFO chatter that buries the application's
# own logs: AMQP link-state churn from the Service Bus receiver, a managed-identity
# token fetch every few seconds, and per-request HTTP logging. Quiet these to
# WARNING. (The http_logging_policy logger can also emit request URLs/headers at
# INFO/DEBUG, so quieting it is a mild security positive as well.)
_NOISY_AZURE_LOGGERS = (
    "azure.servicebus._pyamqp",
    "azure.identity",
    "azure.core.pipeline.policies.http_logging_policy",
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Configure root logging from ``LOG_LEVEL`` and quiet noisy Azure SDK loggers.

    Reads ``LOG_LEVEL`` (default ``INFO``) for the root logger, matching the format
    used across all entrypoints. Safe to call once per process at startup.
    """

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=_LOG_FORMAT,
    )
    for name in _NOISY_AZURE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
