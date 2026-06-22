"""Shared sync job and source event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceFileEvent:
    path: str
    event_type: str
    modified_at: str | None = None
    entry_id: str | None = None


@dataclass(frozen=True)
class SyncJobAccepted:
    job_id: str
    status: str


@dataclass(frozen=True)
class SyncJobMessage:
    job_id: str
    job_type: str
    payload: dict[str, Any] | None = None
    requested_by_user_id: str | None = None
    requested_by_user_name: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class SyncExecutionResult:
    requested_files: int
    downloaded_files: tuple[str, ...]
    updated_wiki_files: tuple[str, ...]
    skipped_files: tuple[str, ...]
    indexed_files: tuple[str, ...]
