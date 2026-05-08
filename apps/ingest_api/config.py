"""Configuration for the ingest API and worker queueing."""

from __future__ import annotations

import os
from dataclasses import dataclass

from packages.wiki_core.settings import CoreSettings


def _read_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class IngestQueueSettings:
    port: int
    service_bus_connection_string: str
    service_bus_namespace: str
    service_bus_queue_name: str
    backend: CoreSettings

    @classmethod
    def from_env(cls) -> "IngestQueueSettings":
        return cls(
            port=int(_read_env("INGEST_API_PORT", "PORT", default="8010")),
            service_bus_connection_string=_read_env("SERVICE_BUS_CONNECTION_STRING"),
            service_bus_namespace=_read_env("SERVICE_BUS_NAMESPACE"),
            service_bus_queue_name=_read_env("INGEST_QUEUE_NAME", default="training-vault-ingest"),
            backend=CoreSettings.from_env(),
        )

    def validate_queue(self) -> None:
        if not self.service_bus_connection_string and not self.service_bus_namespace:
            raise ValueError(
                "Configure either SERVICE_BUS_CONNECTION_STRING or SERVICE_BUS_NAMESPACE for ingest queueing."
            )
        if not self.service_bus_queue_name:
            raise ValueError("INGEST_QUEUE_NAME is required.")
