"""Concept analytics for the Teams bot, persisted to SharePoint lists.

Records which wiki concepts each answered query matched (for the Power BI
dashboard) and user feedback submissions. Fail-soft by design: analytics must
never break or delay an answer — failures are logged and dropped.

Privacy contract: only concept titles, the requester's Teams id/display name,
timestamps, and feedback ratings/comments are recorded. Neither ``record_*``
method accepts question or answer text, so the constraint is structural.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Iterable


LOGGER = logging.getLogger(__name__)

UNKNOWN_CONCEPT = "Unknown"
MAX_CONCEPTS_PER_QUERY = 3


def derive_concepts(citations: Iterable[Any]) -> tuple[str, ...]:
    """Map rank-ordered citations to the concept titles the query was about.

    A citation counts as a concept when its ``page_type`` is ``concept`` (or,
    for index rows built before ``page_type`` existed, its path is under
    ``wiki/concepts/``). Source/entity/index chunks are ignored — there is no
    source-to-concept inverse index today. Dedupes by page, keeps retrieval
    order, caps at ``MAX_CONCEPTS_PER_QUERY``. No concept match (including
    answers with no citations at all) yields ``("Unknown",)``.
    """

    concepts: list[str] = []
    seen: set[str] = set()
    for citation in citations or ():
        path = str(getattr(citation, "path", "") or "")
        page_type = str(getattr(citation, "page_type", "") or "")
        if page_type != "concept" and not path.startswith("wiki/concepts/"):
            continue
        title = str(getattr(citation, "title", "") or "").strip() or "Untitled"
        key = path or title
        if key in seen:
            continue
        seen.add(key)
        concepts.append(title)
        if len(concepts) >= MAX_CONCEPTS_PER_QUERY:
            break
    return tuple(concepts) if concepts else (UNKNOWN_CONCEPT,)


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AnalyticsService:
    """SharePoint-list analytics sink with one-shot lazy initialization.

    Mirrors the ``SourceLinkResolver`` pattern: the Graph client is built on
    first use; if that fails (missing config, no permission), analytics is
    disabled for the process lifetime with a single warning.
    """

    def __init__(self, client: Any = None, settings: Any = None) -> None:
        self._client = client
        self._settings = settings
        self._attempted = client is not None
        self._query_list = getattr(settings, "analytics_query_list_name", "TrainingBotQueryEvents")
        self._feedback_list = getattr(settings, "analytics_feedback_list_name", "TrainingBotFeedback")

    def _get_client(self) -> Any:
        if self._attempted:
            return self._client
        self._attempted = True
        try:
            # Imported lazily so the bot has no hard dependency on SharePoint
            # configuration at import time (same posture as SourceLinkResolver).
            from packages.wiki_core.analytics.sharepoint_lists import SharePointListClient
            from packages.wiki_core.settings import CoreSettings

            settings = self._settings or CoreSettings.from_env()
            if not settings.analytics_enabled:
                LOGGER.info("Concept analytics disabled via ANALYTICS_ENABLED")
                return None
            self._query_list = settings.analytics_query_list_name
            self._feedback_list = settings.analytics_feedback_list_name
            self._client = SharePointListClient(settings)
            LOGGER.info(
                "Concept analytics enabled: lists %r / %r",
                self._query_list,
                self._feedback_list,
            )
        except Exception:
            LOGGER.warning(
                "Concept analytics disabled: SharePoint list client could not be initialized",
                exc_info=True,
            )
            self._client = None
        return self._client

    async def record_query(
        self,
        *,
        request_id: str,
        user_id: str | None,
        user_name: str | None,
        concepts: Iterable[str],
    ) -> None:
        """Record one row per matched concept for an answered query."""

        try:
            client = self._get_client()
            if client is None:
                return
            timestamp = _utc_timestamp()
            for concept in concepts:
                fields = {
                    "Title": concept,
                    "Timestamp": timestamp,
                    "RequestId": request_id,
                    "UserId": user_id or "",
                    "UserName": user_name or "",
                    "Concept": concept,
                    "IsUnknown": concept == UNKNOWN_CONCEPT,
                }
                await asyncio.to_thread(client.create_item, self._query_list, fields)
        except Exception:
            LOGGER.warning(
                "Dropped query analytics event request_id=%s", request_id, exc_info=True
            )

    async def record_feedback(
        self,
        *,
        request_id: str,
        user_id: str | None,
        user_name: str | None,
        rating: str,
        comment: str,
        concepts: Iterable[str],
    ) -> None:
        """Record one row per feedback submission."""

        try:
            client = self._get_client()
            if client is None:
                return
            fields = {
                "Title": rating,
                "Timestamp": _utc_timestamp(),
                "RequestId": request_id,
                "UserId": user_id or "",
                "UserName": user_name or "",
                "Rating": rating,
                "Comment": comment,
                "Concepts": "; ".join(concepts),
            }
            await asyncio.to_thread(client.create_item, self._feedback_list, fields)
        except Exception:
            LOGGER.warning(
                "Dropped feedback analytics event request_id=%s", request_id, exc_info=True
            )
