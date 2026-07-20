"""Background monitor that redraws a single sync-progress card in place.

The sync runs in a separate worker process, so the bot cannot update the card
from within the turn. Instead, for each ``/sync`` the bot posts one progress
card and hands its conversation reference + activity id here; this monitor polls
the ingest API's status endpoint and, when the state advances, uses proactive
messaging (``continue_conversation`` → ``update_activity``) to redraw that same
card. Everything Teams-facing stays in the bot process; the worker only writes a
JSON status file.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from botbuilder.core import TurnContext
from botbuilder.schema import Activity, ActivityTypes, ConversationReference

from packages.wiki_core.ingest.progress import STALE_AFTER_SECONDS, TERMINAL_STATUSES

from teams_bot.cards import build_sync_progress_card
from teams_bot.services.ingest_admin_client import HttpIngestAdminClient

# A status fetcher returns the current job record (or None), and a card builder
# renders it. Parameterizing these lets the same poll/redraw loop drive both the
# sync card and the admin-job (remove/clean/lint) card.
StatusFetcher = Callable[[], Awaitable[dict[str, Any] | None]]
CardBuilder = Callable[..., Any]

LOGGER = logging.getLogger(__name__)

_POLL_SECONDS = 5.0
_MIN_REDRAW_INTERVAL_SECONDS = 10.0
_MAX_LIFETIME_SECONDS = 6 * 3600


def _is_stale_epoch(last_change_monotonic: float) -> bool:
    return (time.monotonic() - last_change_monotonic) > STALE_AFTER_SECONDS


class SyncProgressMonitor:
    def __init__(self, ingest_client: HttpIngestAdminClient) -> None:
        self._ingest_client = ingest_client
        # Strong references so fire-and-forget monitor tasks aren't GC'd mid-run.
        self._tasks: set[asyncio.Task] = set()

    def start(
        self,
        *,
        job_id: str,
        adapter: Any,
        app_id: str,
        conversation_reference: ConversationReference,
        activity_id: str,
        fetch_status: StatusFetcher | None = None,
        build_card: CardBuilder | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run(
                job_id=job_id,
                adapter=adapter,
                app_id=app_id,
                conversation_reference=conversation_reference,
                activity_id=activity_id,
                fetch_status=fetch_status or self._ingest_client.get_sync_status,
                build_card=build_card or build_sync_progress_card,
            )
        )
        self._tasks.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                LOGGER.warning("Sync progress monitor failed", exc_info=finished.exception())

        task.add_done_callback(_done)

    async def _run(
        self,
        *,
        job_id: str,
        adapter: Any,
        app_id: str,
        conversation_reference: ConversationReference,
        activity_id: str,
        fetch_status: StatusFetcher | None = None,
        build_card: CardBuilder | None = None,
    ) -> None:
        fetch_status = fetch_status or self._ingest_client.get_sync_status
        build_card = build_card or build_sync_progress_card
        deadline = time.monotonic() + _MAX_LIFETIME_SECONDS
        last_signature: tuple | None = None
        last_redraw = 0.0
        # Tracks when the worker's heartbeat last advanced, to detect a stall.
        last_heartbeat_change = time.monotonic()
        last_updated_at: str | None = None

        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_SECONDS)
            record = await fetch_status()
            if not record or record.get("status") == "none":
                # No record yet (file not written) — keep the initial card and wait.
                continue

            # A different job means ours was superseded; stop watching so we don't
            # relabel this card with another job's progress.
            record_job_id = str(record.get("job_id") or "")
            if job_id and record_job_id and record_job_id != job_id:
                return

            updated_at = record.get("updated_at")
            if updated_at != last_updated_at:
                last_updated_at = updated_at
                last_heartbeat_change = time.monotonic()

            status = str(record.get("status") or "none")
            terminal = status in TERMINAL_STATUSES
            stalled = not terminal and status == "running" and _is_stale_epoch(last_heartbeat_change)

            signature = (
                status,
                record.get("phase"),
                record.get("files_done"),
                record.get("updated_files"),
                record.get("skipped_unchanged"),
                len(record.get("failed_files") or []),
                stalled,
                bool(record.get("cancel_requested")),
            )
            now = time.monotonic()
            should_redraw = terminal or (
                signature != last_signature and (now - last_redraw) >= _MIN_REDRAW_INTERVAL_SECONDS
            )
            if not should_redraw:
                continue

            redrawn = await self._redraw(
                adapter=adapter,
                app_id=app_id,
                conversation_reference=conversation_reference,
                activity_id=activity_id,
                record=record,
                stalled=stalled,
                build_card=build_card,
            )
            if redrawn:
                last_signature = signature
                last_redraw = now

            if terminal:
                return

    async def _redraw(
        self,
        *,
        adapter: Any,
        app_id: str,
        conversation_reference: ConversationReference,
        activity_id: str,
        record: dict[str, Any],
        stalled: bool,
        build_card: CardBuilder,
    ) -> bool:
        card = build_card(record, stalled=stalled)

        async def _callback(turn_context: TurnContext) -> None:
            activity = Activity(id=activity_id, type=ActivityTypes.message, attachments=[card])
            # update_activity needs conversation.id + service_url; apply the stored
            # reference so the connector call targets the right chat and message.
            TurnContext.apply_conversation_reference(activity, conversation_reference)
            await turn_context.update_activity(activity)

        try:
            await adapter.continue_conversation(conversation_reference, _callback, app_id)
            return True
        except Exception:
            # Throttling (429) or transient auth: keep the last card and let the
            # next tick retry. Never let a redraw failure kill the monitor.
            LOGGER.warning("Failed to update sync progress card", exc_info=True)
            return False
