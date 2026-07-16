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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping


LOGGER = logging.getLogger(__name__)

UNKNOWN_CONCEPT = "Unknown"

# How long a built source->concept map stays fresh. The wiki only changes when
# an ingest sync runs, so brief staleness is acceptable.
CONCEPT_MAP_TTL_SECONDS = 900.0

# Absolute cosine-distance ceiling for the nearest concept: at or below this the
# query is "about" that concept; beyond it the query is off-topic -> Unknown.
# Tuned against text-embedding-3-large: on-topic queries land ~0.7-1.25 from
# their concept, off-topic queries ~1.7+, a wide and stable gap (relevance to
# the nearest concept doesn't drift as the corpus grows). Edit this constant and
# redeploy to re-tune; only the embedding model changing should require it.
DEFAULT_CONCEPT_MAX_DISTANCE = 1.5


@dataclass(frozen=True)
class ConceptMatch:
    """The single wiki concept a query was classified against (title + page path)."""

    title: str
    path: str = ""


UNKNOWN_MATCH = ConceptMatch(UNKNOWN_CONCEPT)


def derive_concept(
    citations: Iterable[Any],
    source_concepts: Mapping[str, tuple[ConceptMatch, ...]] | None = None,
    concept_candidates: Iterable[Mapping[str, Any]] | None = None,
    max_distance: float = DEFAULT_CONCEPT_MAX_DISTANCE,
) -> ConceptMatch:
    """Classify a query as the single most relevant wiki concept.

    Primary signal (when ``concept_candidates`` — the relevance-ordered nearest
    concept-typed chunks from retrieval diagnostics — are available): the single
    nearest concept, accepted when its distance is at or below ``max_distance``,
    else ``Unknown``. This absolute gate cleanly separates on-topic queries (near
    a concept) from off-topic ones and needs no index-heal caveats.

    Fallback (candidates absent, e.g. a legacy text-only backend): the first
    concept-typed citation in retrieval rank order, then the first source
    citation mapped to a concept via ``source_concepts``, then ``Unknown``.

    Returns exactly one ``ConceptMatch`` — the product wants a single label per
    query even when several concepts are relevant.
    """

    if concept_candidates:
        for candidate in concept_candidates:
            distance = candidate.get("distance")
            if not isinstance(distance, (int, float)):
                continue
            # Candidates are distance-ordered, so the first with a numeric
            # distance is the nearest concept — it alone decides the outcome.
            title = str(candidate.get("title") or "").strip()
            if distance <= max_distance and title:
                return ConceptMatch(title, str(candidate.get("path") or ""))
            return UNKNOWN_MATCH
        return UNKNOWN_MATCH

    for citation in citations or ():
        path = str(getattr(citation, "path", "") or "")
        page_type = str(getattr(citation, "page_type", "") or "")
        if page_type == "concept" or path.startswith("wiki/concepts/"):
            title = str(getattr(citation, "title", "") or "").strip() or "Untitled"
            return ConceptMatch(title, path)

    for citation in citations or ():
        path = str(getattr(citation, "path", "") or "")
        matches = (source_concepts or {}).get(path, ())
        if matches:
            return matches[0]

    return UNKNOWN_MATCH


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
        self._mapping: dict[str, tuple[ConceptMatch, ...]] = {}
        self._expires_at = 0.0
        self._warned = False

    def mapping(self) -> Mapping[str, tuple[ConceptMatch, ...]]:
        """Return the current map, rebuilding when the TTL has lapsed.

        Synchronous (a cold build reads every wiki page); call via
        ``asyncio.to_thread`` from async code. Never raises.
        """

        if time.monotonic() < self._expires_at:
            return self._mapping
        try:
            self._mapping = self._build()
            if not self._mapping:
                LOGGER.warning(
                    "Source->concept map built EMPTY (no concept pages with wiki/ sources found "
                    "under the configured wiki root) — source citations will classify as Unknown"
                )
            else:
                LOGGER.info("Source->concept map built with %d source entries", len(self._mapping))
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

    def _build(self) -> dict[str, tuple[ConceptMatch, ...]]:
        # Imported lazily so the bot has no hard dependency on wiki-core
        # configuration at import time (same posture as SourceLinkResolver).
        from packages.wiki_core.content.file_page_store import FilePageStore
        from packages.wiki_core.settings import CoreSettings

        if self._settings is None:
            self._settings = CoreSettings.from_env()
        store = FilePageStore(self._settings)

        mapping: dict[str, list[ConceptMatch]] = {}
        for path in store.iter_wiki_pages():
            try:
                page = store.load_wiki_page(path)
            except Exception:
                LOGGER.debug("Skipping unreadable wiki page %s", path, exc_info=True)
                continue
            if page.page_type != "concept":
                continue
            match = ConceptMatch(page.title.strip() or "Untitled", page.relative_path)
            for source in page.sources:
                normalized = str(source).strip().strip("/")
                # Concept frontmatter can also reference raw/ files; only wiki
                # pages appear as citation paths.
                if not normalized.startswith("wiki/"):
                    continue
                matches = mapping.setdefault(normalized, [])
                if not any(existing.title == match.title for existing in matches):
                    matches.append(match)
        return {source: tuple(matches) for source, matches in mapping.items()}


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
        concept: str,
        concept_title: str,
    ) -> None:
        """Record one row for an answered query's single most-relevant concept.

        ``concept`` is the short dashboard label; ``concept_title`` is the full
        wiki concept title, stored in the built-in Title column.
        """

        try:
            client = self._get_client()
            if client is None:
                return
            fields = {
                "Title": concept_title or concept,
                "Timestamp": _utc_timestamp(),
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
