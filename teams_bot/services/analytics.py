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
import time
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping


LOGGER = logging.getLogger(__name__)

UNKNOWN_CONCEPT = "Unknown"
MAX_CONCEPTS_PER_QUERY = 3

# How long a built source->concept map stays fresh. The wiki only changes when
# an ingest sync runs, so brief staleness is acceptable.
CONCEPT_MAP_TTL_SECONDS = 900.0


def derive_concepts(
    citations: Iterable[Any],
    source_concepts: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """Map rank-ordered citations to the concept titles the query was about.

    A citation counts directly as a concept when its ``page_type`` is
    ``concept`` (or, for index rows built before ``page_type`` existed, its
    path is under ``wiki/concepts/``). Retrieval is dominated by ``source``
    pages, so source citations are mapped back to the concepts that cite them
    via ``source_concepts`` (source wiki path -> concept titles, built by
    ``ConceptMapResolver``). Keeps retrieval rank order, dedupes by title
    (the analytics column is title-keyed), caps at ``MAX_CONCEPTS_PER_QUERY``.
    No concept match (including answers with no citations) yields
    ``("Unknown",)``.
    """

    concepts: list[str] = []
    seen: set[str] = set()

    def _add(title: str) -> bool:
        """Record a concept title; return True once the cap is reached."""

        title = title.strip() or "Untitled"
        if title not in seen:
            seen.add(title)
            concepts.append(title)
        return len(concepts) >= MAX_CONCEPTS_PER_QUERY

    for citation in citations or ():
        if len(concepts) >= MAX_CONCEPTS_PER_QUERY:
            break
        path = str(getattr(citation, "path", "") or "")
        page_type = str(getattr(citation, "page_type", "") or "")
        if page_type == "concept" or path.startswith("wiki/concepts/"):
            _add(str(getattr(citation, "title", "") or ""))
            continue
        for concept_title in (source_concepts or {}).get(path, ()):
            if _add(concept_title):
                break

    return tuple(concepts) if concepts else (UNKNOWN_CONCEPT,)


class ConceptMapResolver:
    """Builds and caches the inverse map {source wiki path -> concept titles}.

    Concept pages' frontmatter ``sources:`` lists the ``wiki/sources/*.md``
    pages they cite, which exactly match source citations' ``path``. This
    inverts that relationship so retrieved source chunks can be attributed to
    concepts. Fail-soft like ``SourceLinkResolver``: a failed build keeps the
    last good map (initially empty) and retries after the TTL — analytics
    degrades to Unknown-heavy classification, answers are never affected.
    """

    def __init__(self, settings: Any = None, ttl_seconds: float = CONCEPT_MAP_TTL_SECONDS) -> None:
        self._settings = settings
        self._ttl_seconds = ttl_seconds
        self._mapping: dict[str, tuple[str, ...]] = {}
        self._expires_at = 0.0
        self._warned = False

    def mapping(self) -> Mapping[str, tuple[str, ...]]:
        """Return the current map, rebuilding when the TTL has lapsed.

        Synchronous (a cold build reads every wiki page); call via
        ``asyncio.to_thread`` from async code. Never raises.
        """

        if time.monotonic() < self._expires_at:
            return self._mapping
        try:
            self._mapping = self._build()
            if self._warned:
                LOGGER.info("Source->concept map recovered with %d source entries", len(self._mapping))
            self._warned = False
        except Exception:
            if not self._warned:
                LOGGER.warning(
                    "Could not build the source->concept map; keeping the previous mapping",
                    exc_info=True,
                )
                self._warned = True
        self._expires_at = time.monotonic() + self._ttl_seconds
        return self._mapping

    def _build(self) -> dict[str, tuple[str, ...]]:
        # Imported lazily so the bot has no hard dependency on wiki-core
        # configuration at import time (same posture as SourceLinkResolver).
        from packages.wiki_core.content.file_page_store import FilePageStore
        from packages.wiki_core.settings import CoreSettings

        if self._settings is None:
            self._settings = CoreSettings.from_env()
        store = FilePageStore(self._settings)

        mapping: dict[str, list[str]] = {}
        for path in store.iter_wiki_pages():
            try:
                page = store.load_wiki_page(path)
            except Exception:
                LOGGER.debug("Skipping unreadable wiki page %s", path, exc_info=True)
                continue
            if page.page_type != "concept":
                continue
            title = page.title.strip() or "Untitled"
            for source in page.sources:
                normalized = str(source).strip().strip("/")
                # Concept frontmatter can also reference raw/ files; only wiki
                # pages appear as citation paths.
                if not normalized.startswith("wiki/"):
                    continue
                titles = mapping.setdefault(normalized, [])
                if title not in titles:
                    titles.append(title)
        return {source: tuple(titles) for source, titles in mapping.items()}


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
