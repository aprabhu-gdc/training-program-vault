"""MCP tools for the Claude analytics connector.

Three tools, all identity-bound to the signed-in caller:

* ``record_question``  - one QueryEvents row per answered question (concept only)
* ``submit_feedback``  - one Feedback row (helpful / inaccurate + optional comment)
* ``get_analytics``    - dashboard figures; callers need the admin app role

Privacy contract (unchanged from the Teams bot): no tool accepts the user's
question or Claude's answer text. ``record_question`` takes only the cited wiki
page path/title, validated in ``concepts.py``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import OrderedDict, deque
from datetime import date
from typing import Annotated, Any, Awaitable, Callable, Literal, Protocol

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from apps.analytics_mcp.concepts import ConceptInputError, resolve_concept
from apps.analytics_mcp.identity import Caller, NotSignedInError
from apps.analytics_mcp.report import ReportFilter, build_report


LOGGER = logging.getLogger(__name__)

MAX_COMMENT_CHARS = 1000
FEEDBACK_CACHE_SIZE = 5000
REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

SERVER_INSTRUCTIONS = """\
Graydaze training analytics. Records which wiki topics people ask about so the
training team can see what PMs need. It never stores question or answer text.

After you answer a question from the Graydaze training wiki, call
record_question exactly once with the path and title of the single wiki page
that best matches the question (prefer a page under wiki/concepts/). If the
wiki did not cover the question, call record_question with no arguments.

When the person says an answer was helpful or wrong/inaccurate, call
submit_feedback with the request_id from record_question, and their comment if
they gave one. Never put the question or your answer into any argument.
"""


class AnalyticsSink(Protocol):
    async def record_query(
        self, *, request_id: str, user_id: str | None, user_name: str | None, concept: str, concept_title: str
    ) -> None: ...

    async def record_feedback(
        self,
        *,
        request_id: str,
        user_id: str | None,
        user_name: str | None,
        rating: str,
        comment: str,
        concepts: Any,
    ) -> None: ...


# Returns (query_rows, feedback_rows, truncated).
AnalyticsSource = Callable[[], Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]], bool]]]
CallerResolver = Callable[[], Caller]


class _RateLimiter:
    """Per-user sliding window; protects the lists from runaway tool loops."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if self._per_minute <= 0:
            return True
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= self._per_minute:
            return False
        hits.append(now)
        return True


def default_caller_resolver(tenant_id: str) -> CallerResolver:
    def resolve() -> Caller:
        from fastmcp.server.dependencies import get_access_token

        from apps.analytics_mcp.identity import caller_from_claims

        token = get_access_token()
        return caller_from_claims(token.claims if token else None, tenant_id=tenant_id)

    return resolve


def build_server(
    *,
    sink: AnalyticsSink,
    source: AnalyticsSource,
    caller_resolver: CallerResolver,
    admin_role: str = "Analytics.Admin",
    auth: Any = None,
    record_limit_per_minute: int = 30,
) -> FastMCP:
    mcp = FastMCP("Graydaze Training Analytics", instructions=SERVER_INSTRUCTIONS, auth=auth)
    limiter = _RateLimiter(record_limit_per_minute)
    # request_id -> (user_id, concept label). Lets feedback carry the concept
    # label and stops one person rating another person's question. Lost on
    # restart, in which case feedback falls back to the concept Claude passes.
    asked: OrderedDict[str, tuple[str, str]] = OrderedDict()

    def caller() -> Caller:
        try:
            return caller_resolver()
        except NotSignedInError as exc:
            raise ToolError(f"Sign-in required: {exc}") from exc

    def remember(request_id: str, user_id: str, label: str) -> None:
        asked[request_id] = (user_id, label)
        asked.move_to_end(request_id)
        while len(asked) > FEEDBACK_CACHE_SIZE:
            asked.popitem(last=False)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Record question topic", read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    async def record_question(
        page_path: Annotated[
            str | None,
            Field(
                description="Path of the single wiki page that best matches the question, e.g. "
                "'wiki/concepts/retainage.md'. Omit when the wiki did not cover the question."
            ),
        ] = None,
        page_title: Annotated[
            str | None,
            Field(description="That page's title, e.g. 'Retainage'. Never the question text."),
        ] = None,
    ) -> dict[str, Any]:
        """Record which wiki topic a question was about. Call once per answered question."""

        who = caller()
        if not limiter.allow(who.user_id):
            raise ToolError("Too many analytics records in the last minute; try again shortly.")
        try:
            concept = resolve_concept(page_path, page_title)
        except ConceptInputError as exc:
            raise ToolError(str(exc)) from exc

        request_id = uuid.uuid4().hex
        await sink.record_query(
            request_id=request_id,
            user_id=who.user_id,
            user_name=who.user_name,
            concept=concept.label,
            concept_title=concept.title,
        )
        remember(request_id, who.user_id, concept.label)
        return {"request_id": request_id, "concept": concept.label}

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Submit answer feedback", read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    async def submit_feedback(
        request_id: Annotated[str, Field(description="The request_id returned by record_question.")],
        rating: Annotated[Literal["helpful", "inaccurate"], Field(description="The person's rating.")],
        comment: Annotated[
            str,
            Field(description="The person's own feedback comment, if they gave one.", max_length=MAX_COMMENT_CHARS),
        ] = "",
        page_path: Annotated[
            str | None,
            Field(description="The page_path passed to record_question for this answer, if known."),
        ] = None,
    ) -> dict[str, Any]:
        """Record whether an answer was helpful or inaccurate, with an optional comment."""

        who = caller()
        request_id = request_id.strip().lower()
        if not REQUEST_ID_PATTERN.match(request_id):
            raise ToolError("request_id must be the value returned by record_question.")
        if not limiter.allow(who.user_id):
            raise ToolError("Too many analytics records in the last minute; try again shortly.")

        label = ""
        known = asked.get(request_id)
        if known:
            owner_id, label = known
            if owner_id != who.user_id:
                raise ToolError("You can only give feedback on your own questions.")
        elif page_path:
            try:
                label = resolve_concept(page_path, None).label
            except ConceptInputError:
                label = ""

        await sink.record_feedback(
            request_id=request_id,
            user_id=who.user_id,
            user_name=who.user_name,
            rating=rating,
            comment=comment.strip()[:MAX_COMMENT_CHARS],
            concepts=[label] if label else [],
        )
        return {"recorded": True, "rating": rating}

    admin_check = None
    if auth is not None:
        from fastmcp.utilities.authorization import require_roles

        admin_check = require_roles(admin_role, extract=lambda claims: claims["roles"])

    @mcp.tool(
        annotations=ToolAnnotations(title="Training analytics report", read_only_hint=True, open_world_hint=False),
        auth=admin_check,
    )
    async def get_analytics(
        start_date: Annotated[date | None, Field(description="First day to include (UTC), YYYY-MM-DD.")] = None,
        end_date: Annotated[date | None, Field(description="Last day to include (UTC), YYYY-MM-DD.")] = None,
        user_id: Annotated[str | None, Field(description="Limit to one user (user_id from the users list).")] = None,
    ) -> dict[str, Any]:
        """Training analytics for admins: questions, unknown questions, topics, feedback, and users."""

        who = caller()
        if not who.has_role(admin_role):
            raise ToolError("This report is limited to training admins.")
        if start_date and end_date and start_date > end_date:
            raise ToolError("start_date must be on or before end_date.")
        try:
            query_rows, feedback_rows, truncated = await source()
        except Exception as exc:
            LOGGER.warning("Could not read analytics lists", exc_info=True)
            raise ToolError("Could not read the analytics lists right now.") from exc
        return build_report(
            query_rows,
            feedback_rows,
            ReportFilter(start_date, end_date, (user_id or "").strip() or None),
            truncated=truncated,
        )

    return mcp


class CachedListSource:
    """Reads both SharePoint lists, cached briefly so dashboard refreshes stay cheap."""

    def __init__(self, client: Any, query_list: str, feedback_list: str, ttl_seconds: float = 60.0) -> None:
        self._client = client
        self._query_list = query_list
        self._feedback_list = feedback_list
        self._ttl = ttl_seconds
        self._cached: tuple[list[dict[str, Any]], list[dict[str, Any]], bool] | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        async with self._lock:
            if self._cached is not None and time.monotonic() < self._expires_at:
                return self._cached
            from apps.analytics_mcp.report import FEEDBACK_FIELDS, QUERY_FIELDS

            queries, q_truncated = await asyncio.to_thread(
                self._client.list_items, self._query_list, QUERY_FIELDS
            )
            feedback, f_truncated = await asyncio.to_thread(
                self._client.list_items, self._feedback_list, FEEDBACK_FIELDS
            )
            self._cached = (queries, feedback, q_truncated or f_truncated)
            self._expires_at = time.monotonic() + self._ttl
            return self._cached
