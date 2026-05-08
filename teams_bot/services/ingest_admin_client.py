"""HTTP admin client for remote ingest operations."""

from __future__ import annotations

import aiohttp

from packages.contracts.sync import SyncJobAccepted

from .wiki_query import WikiIntegrationError


class HttpIngestAdminClient:
    def __init__(self, base_url: str, timeout_seconds: float = 45.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def request_manual_sync(
        self,
        *,
        requested_by_user_id: str | None,
        requested_by_user_name: str | None,
    ) -> SyncJobAccepted:
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
                    response.raise_for_status()
                    payload = await response.json()
        except aiohttp.ClientError as exc:
            raise WikiIntegrationError("Remote ingest admin request failed.") from exc

        if not isinstance(payload, dict):
            raise WikiIntegrationError("Remote ingest admin returned an unexpected payload.")

        return SyncJobAccepted(
            job_id=str(payload.get("job_id") or ""),
            status=str(payload.get("status") or "accepted"),
        )
