"""Keeps the SharePoint drive change-notification subscription alive.

Graph driveItem subscriptions expire (~24h default), so something must create
one on startup and renew it before expiry — otherwise uploads to raw/sources
stop triggering ingest silently. The ingest API hosts an hourly loop that
calls ``SubscriptionManager.ensure_once``; the worker's periodic full-sync
sweep covers any gap this loop misses.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)

# Renew when the subscription expires within this window. Checks run hourly,
# so 6h gives several chances before a ~24h subscription actually lapses.
RENEW_WHEN_EXPIRING_WITHIN = timedelta(hours=6)


def parse_graph_datetime(value: Any) -> datetime | None:
    """Parse Graph ISO timestamps, tolerating 7-digit fractional seconds.

    Graph emits e.g. ``2026-07-15T15:00:00.0000000Z``, whose 7-digit fraction
    ``datetime.fromisoformat`` rejects on some Python versions. Returns None
    for anything unparseable (callers treat that as "renew now").
    """

    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        offset = ""
        for index, char in enumerate(tail):
            if char.isdigit():
                digits += char
            else:
                offset = tail[index:]
                break
        text = f"{head}.{(digits[:6] or '0')}{offset}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class SubscriptionManager:
    """Idempotently maintains the Graph subscription for the webhook URL.

    ``ensure_once`` never raises: unconfigured environments skip with a single
    INFO line, and Graph failures log a warning and retry on the next cycle.
    """

    def __init__(self, adapter_factory: Callable[[], Any], settings: Any) -> None:
        self._adapter_factory = adapter_factory
        self._settings = settings
        self._logged_disabled = False

    @property
    def configured(self) -> bool:
        return bool(
            str(self._settings.sharepoint_webhook_notification_url or "").strip()
            and str(self._settings.sharepoint_webhook_client_state or "").strip()
        )

    def ensure_once(self) -> str:
        """Run one check cycle; returns skipped | created | renewed | ok | failed."""

        if not self.configured:
            if not self._logged_disabled:
                LOGGER.info(
                    "SharePoint webhook subscription management disabled "
                    "(SHAREPOINT_WEBHOOK_NOTIFICATION_URL / SHAREPOINT_WEBHOOK_CLIENT_STATE unset)"
                )
                self._logged_disabled = True
            return "skipped"

        try:
            adapter = self._adapter_factory()
            notification_url = str(self._settings.sharepoint_webhook_notification_url).strip()
            resource = adapter.subscription_resource().strip("/")

            match: dict[str, Any] | None = None
            for subscription in adapter.list_subscriptions():
                if str(subscription.get("notificationUrl") or "").strip() != notification_url:
                    continue
                if str(subscription.get("resource") or "").strip("/") != resource:
                    continue
                match = subscription
                break

            if match is None:
                created = adapter.create_subscription()
                LOGGER.info(
                    "Created SharePoint webhook subscription id=%s expires=%s",
                    created.get("id"),
                    created.get("expirationDateTime"),
                )
                return "created"

            expiry = parse_graph_datetime(match.get("expirationDateTime"))
            if expiry is None or expiry - datetime.now(UTC) < RENEW_WHEN_EXPIRING_WITHIN:
                renewed = adapter.renew_subscription(str(match.get("id") or ""))
                LOGGER.info(
                    "Renewed SharePoint webhook subscription id=%s expires=%s",
                    match.get("id"),
                    renewed.get("expirationDateTime"),
                )
                return "renewed"

            return "ok"
        except Exception:
            LOGGER.warning(
                "SharePoint webhook subscription check failed; retrying next cycle",
                exc_info=True,
            )
            return "failed"
