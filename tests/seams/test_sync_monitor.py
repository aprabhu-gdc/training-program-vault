"""SyncProgressMonitor drives proactive card redraws and stops correctly."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import teams_bot.services.sync_monitor as sm
from teams_bot.services.sync_monitor import SyncProgressMonitor


class _FakeAdapter:
    """continue_conversation(reference, callback, app_id) invokes the callback
    with a turn context whose update_activity records the drawn activity."""

    def __init__(self):
        self.updated_activities = []

    async def continue_conversation(self, reference, callback, app_id):
        ctx = MagicMock()

        async def _update(activity):
            self.updated_activities.append(activity)

        ctx.update_activity = _update
        await callback(ctx)


def _client_returning(records):
    client = MagicMock()
    seq = iter(records)

    async def _get_status():
        try:
            return next(seq)
        except StopIteration:
            return records[-1]

    client.get_sync_status = _get_status
    return client


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    # Remove real waits/throttle so the loop advances immediately in tests.
    monkeypatch.setattr(sm, "_POLL_SECONDS", 0)
    monkeypatch.setattr(sm, "_MIN_REDRAW_INTERVAL_SECONDS", 0)


async def test_monitor_draws_terminal_card_and_stops():
    adapter = _FakeAdapter()
    client = _client_returning(
        [
            {"job_id": "j", "status": "running", "phase": "processing", "files_total": 2, "files_done": 1, "updated_at": "t1"},
            {"job_id": "j", "status": "completed", "phase": "done", "updated_files": 2, "updated_at": "t2"},
        ]
    )
    monitor = SyncProgressMonitor(client)

    await monitor._run(
        job_id="j",
        adapter=adapter,
        app_id="app",
        conversation_reference=MagicMock(),
        activity_id="act-1",
    )

    # At least the terminal card was drawn, and every draw targeted our activity.
    assert adapter.updated_activities
    assert all(a.id == "act-1" for a in adapter.updated_activities)
    last = adapter.updated_activities[-1]
    import json

    assert "complete" in json.dumps(last.attachments[0].content).lower()


async def test_monitor_stops_when_job_superseded():
    adapter = _FakeAdapter()
    client = _client_returning([{"job_id": "other", "status": "running", "updated_at": "t"}])
    monitor = SyncProgressMonitor(client)

    await monitor._run(
        job_id="mine",
        adapter=adapter,
        app_id="app",
        conversation_reference=MagicMock(),
        activity_id="act-1",
    )

    # A different job_id in the status file means ours is gone; draw nothing.
    assert adapter.updated_activities == []


async def test_monitor_survives_redraw_errors():
    class _BoomAdapter:
        async def continue_conversation(self, reference, callback, app_id):
            raise RuntimeError("429 throttled")

    client = _client_returning([{"job_id": "j", "status": "completed", "updated_at": "t"}])
    monitor = SyncProgressMonitor(client)

    # Must not raise even though every redraw fails.
    await monitor._run(
        job_id="j",
        adapter=_BoomAdapter(),
        app_id="app",
        conversation_reference=MagicMock(),
        activity_id="act-1",
    )
