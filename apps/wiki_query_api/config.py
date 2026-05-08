"""Configuration for the standalone wiki query API."""

from __future__ import annotations

from dataclasses import dataclass

from packages.wiki_core.settings import CoreSettings


@dataclass(frozen=True)
class QueryApiSettings:
    port: int
    backend: CoreSettings

    @classmethod
    def from_env(cls) -> "QueryApiSettings":
        backend = CoreSettings.from_env()
        return cls(port=int(__import__("os").getenv("QUERY_API_PORT", __import__("os").getenv("PORT", "8000"))), backend=backend)
