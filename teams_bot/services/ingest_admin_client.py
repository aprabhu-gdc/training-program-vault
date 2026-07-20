"""HTTP admin client for remote ingest operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .wiki_query import WikiIntegrationError


@dataclass(frozen=True)
class SyncSubmitResult:
    """Outcome of a manual-sync request.

    ``status`` is "accepted" when a new job was queued or "already_running" when
    a sync was already in flight (the ingest API returns 409 with the current
    job's progress so the bot can attach a live card to it).
    """

    job_id: str
    status: str
    progress: dict[str, Any] = field(default_factory=dict)

    @property
    def already_running(self) -> bool:
        return self.status == "already_running"


@dataclass(frozen=True)
class CancelSubmitResult:
    """Outcome of a sync-cancel request.

    ``status`` is one of: ``"cancel_requested"`` (a running/queued sync will stop
    after the current file), ``"cancelled_stale"`` (a stalled sync was marked
    cancelled in place), or ``"no_active_sync"`` (nothing to cancel).
    """

    job_id: str
    status: str
    progress: dict[str, Any] = field(default_factory=dict)

    @property
    def no_active_sync(self) -> bool:
        return self.status == "no_active_sync"

    @property
    def cancelled_stale(self) -> bool:
        return self.status == "cancelled_stale"


class HttpIngestAdminClient:
    def __init__(self, base_url: str, timeout_seconds: float = 45.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def request_manual_sync(
        self,
        *,
        requested_by_user_id: str | None,
        requested_by_user_name: str | None,
    ) -> SyncSubmitResult:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/admin/sync",
                    json={
                        "requested_by_user_id": requested_by_user_id,
                        "requested_by_user_name": requested_by_user_name,
                    },
                ) as response:
                    # 409 is an expected outcome (a sync is already running), not
                    # an error — read its body instead of raising.
                    if response.status == 409:
                        payload = await response.json()
                        progress = payload.get("progress") if isinstance(payload, dict) else {}
                        return SyncSubmitResult(
                            job_id=str((progress or {}).get("job_id") or ""),
                            status="already_running",
                            progress=progress or {},
                        )
                    response.raise_for_status()
                    payload = await response.json()
        except aiohttp.ClientError as exc:
            raise WikiIntegrationError("Remote ingest admin request failed.") from exc

        if not isinstance(payload, dict):
            raise WikiIntegrationError("Remote ingest admin returned an unexpected payload.")

        return SyncSubmitResult(
            job_id=str(payload.get("job_id") or ""),
            status=str(payload.get("status") or "accepted"),
        )

    async def request_cancel(
        self,
        *,
        requested_by_user_name: str | None,
    ) -> CancelSubmitResult:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/admin/sync/cancel",
                    json={"requested_by_user_name": requested_by_user_name},
                ) as response:
                    # 404 is an expected outcome (nothing running), not an error.
                    if response.status == 404:
                        return CancelSubmitResult(job_id="", status="no_active_sync")
                    response.raise_for_status()
                    payload = await response.json()
        except aiohttp.ClientError as exc:
            raise WikiIntegrationError("Remote ingest admin cancel request failed.") from exc

        if not isinstance(payload, dict):
            raise WikiIntegrationError("Remote ingest admin returned an unexpected payload.")

        progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
        return CancelSubmitResult(
            job_id=str(payload.get("job_id") or ""),
            status=str(payload.get("status") or "cancel_requested"),
            progress=progress or {},
        )

    async def request_admin_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        requested_by_user_id: str | None,
        requested_by_user_name: str | None,
    ) -> SyncSubmitResult:
        """Queue a remove/clean/lint job. ``status`` is "accepted",
        "already_running", or "sync_running" (both 409 outcomes carry progress)."""
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/admin/jobs",
                    json={
                        "job_type": job_type,
                        "payload": payload or {},
                        "requested_by_user_id": requested_by_user_id,
                        "requested_by_user_name": requested_by_user_name,
                    },
                ) as response:
                    if response.status == 409:
                        parsed = await response.json()
                        progress = parsed.get("progress") if isinstance(parsed, dict) else {}
                        return SyncSubmitResult(
                            job_id=str((progress or {}).get("job_id") or ""),
                            status=str(parsed.get("status") or "already_running") if isinstance(parsed, dict) else "already_running",
                            progress=progress or {},
                        )
                    response.raise_for_status()
                    payload_out = await response.json()
        except aiohttp.ClientError as exc:
            raise WikiIntegrationError("Remote ingest admin job request failed.") from exc

        if not isinstance(payload_out, dict):
            raise WikiIntegrationError("Remote ingest admin returned an unexpected payload.")
        return SyncSubmitResult(
            job_id=str(payload_out.get("job_id") or ""),
            status=str(payload_out.get("status") or "accepted"),
        )

    async def get_sync_status(self) -> dict[str, Any] | None:
        """Return the current sync progress record, or None if unavailable.

        Tolerant by design: the progress monitor polls this on a loop and simply
        keeps the last card state when a poll fails.
        """

        return await self._get_status("/admin/sync/status")

    async def get_admin_job_status(self) -> dict[str, Any] | None:
        """Return the current admin-job progress record, or None if unavailable."""
        return await self._get_status("/admin/jobs/status")

    async def _get_status(self, route: str) -> dict[str, Any] | None:
        timeout = aiohttp.ClientTimeout(total=5.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._base_url}{route}") as response:
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError):
            return None
        return payload if isinstance(payload, dict) else None
