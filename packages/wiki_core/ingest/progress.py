"""Cross-process progress reporting for source sync jobs.

The sync worker writes a single JSON record describing the in-flight job after
each phase change and each file. The ingest API reads it (to serve status and to
reject duplicate syncs) and the Teams bot polls it (to redraw a live progress
card). Bot and worker share the App Service container filesystem, so a local
JSON file under LOCAL_DATA_ROOT is the simplest store both can reach.

The record contains only file paths, counts, and error class strings — never
config or secret values (org data-security policy).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Cap the per-file lists embedded in the record so a huge sync can't bloat the
# file (and the downstream Adaptive Card). The counts stay exact regardless.
_MAX_LISTED_FILES = 50

# A record whose heartbeat (updated_at) is older than this is treated as stale —
# the worker almost certainly died or restarted mid-run.
STALE_AFTER_SECONDS = 600.0

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}


class SyncCancelledError(Exception):
    """Raised inside a sync when a cooperative cancel has been requested.

    Defined here (not in the worker/ingest modules) so both can import it without
    a cycle. The worker treats this as a *clean* stop: it writes a terminal
    "cancelled" record and completes the Service Bus message rather than
    abandoning it (which would redeliver and restart the sync).
    """


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_progress(path: Path) -> dict[str, Any] | None:
    """Return the current progress record, or None if absent/unreadable."""

    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_stale(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True when an active record's heartbeat is older than STALE_AFTER_SECONDS."""

    if record.get("status") not in ACTIVE_STATUSES:
        return False
    updated_at = record.get("updated_at")
    if not updated_at:
        return True
    try:
        stamp = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return True
    reference = now or datetime.now(UTC)
    return (reference - stamp).total_seconds() > STALE_AFTER_SECONDS


def write_queued(path: Path, *, job_id: str, job_type: str, requested_by_user_name: str | None) -> None:
    """Write an initial 'queued' record when a job is enqueued (best-effort)."""

    record = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "phase": "queued",
        "requested_by_user_name": requested_by_user_name,
        "files_total": 0,
        "files_done": 0,
        "updated_files": 0,
        "skipped_unchanged": 0,
        "empty_files": 0,
        "failed_files": [],
        "unsupported_files": {},
        "current_file": None,
        "started_at": None,
        "updated_at": _now_iso(),
        "finished_at": None,
        "error": None,
        "result": None,
    }
    _atomic_write(path, record)


def _atomic_write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# --- Cooperative sync cancellation -------------------------------------------
# The cancel request lives in its own sentinel file (not the progress record),
# because the worker rewrites the whole progress record on every heartbeat and
# would clobber a flag stored there. The sentinel is keyed by job_id so a stale
# request can never cancel a *different* (later) sync.


def write_cancel(path: Path, *, job_id: str, requested_by_user_name: str | None) -> None:
    """Request cancellation of the given job (best-effort, idempotent)."""

    _atomic_write(
        path,
        {
            "job_id": job_id,
            "requested_at": _now_iso(),
            "requested_by_user_name": requested_by_user_name,
        },
    )


def read_cancel(path: Path) -> dict[str, Any] | None:
    """Return the current cancel request, or None if absent/unreadable."""

    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def clear_cancel(path: Path) -> None:
    """Remove the cancel sentinel (best-effort)."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.debug("Failed to clear cancel sentinel at %s", path, exc_info=True)


def cancel_requested_for(path: Path, job_id: str) -> bool:
    """True when a cancel sentinel exists and targets exactly this job_id."""

    record = read_cancel(path)
    return bool(record) and record.get("job_id") == job_id


def write_cancelled_from(path: Path, record: dict[str, Any], *, error: str | None = None) -> dict[str, Any]:
    """Overwrite a progress record as terminally 'cancelled' (for stale jobs).

    Used by the API when a sync has stopped heart-beating: the worker will never
    write a terminal record itself, so the status must be corrected in place to
    keep the progress card truthful.
    """

    updated = dict(record)
    updated["status"] = "cancelled"
    updated["phase"] = "cancelled"
    updated["current_file"] = None
    updated["finished_at"] = _now_iso()
    updated["updated_at"] = _now_iso()
    if error is not None:
        updated["error"] = error
    _atomic_write(path, updated)
    return updated


class ProgressReporter:
    """No-op reporter. The default so existing callers need no changes."""

    def start(self, *, requested_by_user_name: str | None = None) -> None: ...
    def phase(self, name: str) -> None: ...
    def set_total(self, total: int) -> None: ...
    def set_unsupported(self, unsupported: dict[str, int]) -> None: ...
    def begin_file(self, path: str) -> None: ...
    def record(self, outcome: str, *, path: str | None = None, error: str | None = None) -> None: ...
    def set_result(self, result: dict[str, Any]) -> None: ...
    def finish_ok(self) -> None: ...
    def finish_error(self, message: str) -> None: ...
    def finish_cancelled(self) -> None: ...

    def should_cancel(self) -> bool:
        """Whether a cooperative cancel has been requested for this job."""
        return False


class FileProgressReporter(ProgressReporter):
    """Writes the shared progress record after each phase change and each file.

    ~2 local atomic writes per file over a few hundred files is negligible, and
    keeps updated_at advancing so a watching client can tell a slow-but-healthy
    sync from a stalled one.
    """

    def __init__(
        self,
        path: Path,
        *,
        job_id: str,
        job_type: str,
        requested_by_user_name: str | None = None,
        cancel_path: Path | None = None,
    ) -> None:
        self._path = path
        self._cancel_path = cancel_path
        self._job_id = job_id
        self._record: dict[str, Any] = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "phase": "queued",
            "requested_by_user_name": requested_by_user_name,
            "files_total": 0,
            "files_done": 0,
            "updated_files": 0,
            "skipped_unchanged": 0,
            "empty_files": 0,
            "failed_files": [],
            "unsupported_files": {},
            "current_file": None,
            "started_at": None,
            "updated_at": _now_iso(),
            "finished_at": None,
            "error": None,
            "result": None,
        }

    def should_cancel(self) -> bool:
        if self._cancel_path is None:
            return False
        return cancel_requested_for(self._cancel_path, self._job_id)

    def start(self, *, requested_by_user_name: str | None = None) -> None:
        if requested_by_user_name is not None:
            self._record["requested_by_user_name"] = requested_by_user_name
        self._record["status"] = "running"
        self._record["phase"] = "starting"
        self._record["started_at"] = _now_iso()
        self._flush()

    def phase(self, name: str) -> None:
        self._record["phase"] = name
        self._record["current_file"] = None
        self._flush()

    def set_total(self, total: int) -> None:
        self._record["files_total"] = int(total)
        self._flush()

    def set_unsupported(self, unsupported: dict[str, int]) -> None:
        self._record["unsupported_files"] = dict(unsupported)
        self._flush()

    def begin_file(self, path: str) -> None:
        self._record["current_file"] = path
        self._flush()

    def record(self, outcome: str, *, path: str | None = None, error: str | None = None) -> None:
        if outcome == "updated":
            self._record["updated_files"] += 1
        elif outcome == "skipped_unchanged":
            self._record["skipped_unchanged"] += 1
        elif outcome == "empty":
            self._record["empty_files"] += 1
        elif outcome == "failed":
            failed = self._record["failed_files"]
            if len(failed) < _MAX_LISTED_FILES:
                failed.append({"path": path or "", "error": error or "unknown error"})

        # files_done counts every file examined (including unchanged skips) so the
        # progress bar tracks position through the file list and reaches 100% even
        # on a no-op resync where most files are unchanged.
        self._record["files_done"] += 1
        self._flush()

    def set_result(self, result: dict[str, Any]) -> None:
        """Attach a job-type-specific summary, preserved through finish_ok().

        Paths and counts only — never page content or secrets (data-security).
        """
        self._record["result"] = dict(result)
        self._flush()

    def finish_ok(self) -> None:
        self._record["status"] = "completed"
        self._record["phase"] = "done"
        self._record["current_file"] = None
        self._record["finished_at"] = _now_iso()
        self._flush()

    def finish_error(self, message: str) -> None:
        self._record["status"] = "failed"
        self._record["current_file"] = None
        self._record["error"] = message
        self._record["finished_at"] = _now_iso()
        self._flush()

    def finish_cancelled(self) -> None:
        self._record["status"] = "cancelled"
        self._record["phase"] = "cancelled"
        self._record["current_file"] = None
        self._record["finished_at"] = _now_iso()
        self._flush()

    def _flush(self) -> None:
        self._record["updated_at"] = _now_iso()
        try:
            _atomic_write(self._path, self._record)
        except OSError:
            # Progress reporting must never break the sync it is describing.
            LOGGER.warning("Failed to write sync progress to %s", self._path, exc_info=True)
