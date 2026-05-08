"""Interfaces for source synchronization adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from packages.contracts.sync import SourceFileEvent


class SourceSyncAdapter(Protocol):
    def parse_webhook_payload(self, payload: Any) -> list[SourceFileEvent]: ...

    def is_in_scope(self, event: SourceFileEvent) -> bool: ...

    def download_file(self, path: str) -> Path: ...

    def list_files_recursive(self, root_path: str) -> list[SourceFileEvent]: ...
