"""Feedback logging hook for Adaptive Card responses.

The default implementation only writes structured logs. Replace or extend this
service if you want to persist feedback into Application Insights, a database,
    queue, analytics pipeline, or your existing backend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackEvent:
    """Structured feedback payload from the Teams Adaptive Card."""

    request_id: str
    feedback: str
    user_id: str | None
    user_name: str | None
    conversation_id: str | None
    tenant_id: str | None
    channel_id: str | None


class FeedbackLogger:
    """Minimal feedback logger abstraction.

    This keeps the bot handler production-friendly: operators can swap in a real
    persistence layer later without rewriting message-processing code.
    """

    async def log(self, event: FeedbackEvent) -> None:
        """Record a feedback event.

        The default behavior is intentionally simple and safe: emit structured
        logs that are easy to scrape in cloud logging systems.
        """

        LOGGER.info(
            "feedback request_id=%s feedback=%s user_id=%s user_name=%s conversation_id=%s tenant_id=%s channel_id=%s",
            event.request_id,
            event.feedback,
            event.user_id,
            event.user_name,
            event.conversation_id,
            event.tenant_id,
            event.channel_id,
        )
