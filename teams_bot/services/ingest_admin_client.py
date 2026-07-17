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

    async def get_sync_status(self) -> dict[str, Any] | None:
        """Return the current sync progress record, or None if unavailable.

        Tolerant by design: the progress monitor polls this on a loop and simply
        keeps the last card state when a poll fails.
        """

        timeout = aiohttp.ClientTimeout(total=5.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._base_url}/admin/sync/status") as response:
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError):
            return None
        return payload if isinstance(payload, dict) else None
