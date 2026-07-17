"""Ingest API admin-job and sync-cancel endpoints.

Service Bus is mocked (send_json_message recorder); CoreSettings is patched to an
offline tmp-dir instance so progress/cancel files land under tmp_path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

import apps.ingest_api.app as ingest_app
from packages.wiki_core.ingest.progress import FileProgressReporter, read_cancel
from packages.wiki_core.settings import CoreSettings
from tests.conftest import make_core_settings


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://test/;SharedAccessKeyName=k;SharedAccessKey=v")
    monkeypatch.setenv("INGEST_QUEUE_NAME", "test-queue")
    monkeypatch.delenv("SERVICE_BUS_NAMESPACE", raising=False)
    offline = make_core_settings(tmp_path)
    monkeypatch.setattr(CoreSettings, "from_env", classmethod(lambda cls: offline))
    records: list[dict] = []
    monkeypatch.setattr(ingest_app, "send_json_message", lambda **kw: records.append(kw["payload"]))
    return offline, records


# --- /admin/jobs ---------------------------------------------------------------

async def test_submit_lint_job_queues_and_writes_progress(env):
    settings, records = env
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/jobs", json={"job_type": "lint", "requested_by_user_name": "Dana"})
        assert resp.status == 202
        status = await (await client.get("/admin/jobs/status")).json()
        assert status["status"] == "queued"
        assert status["job_type"] == "lint"
    assert records and records[0]["job_type"] == "lint"
    assert records[0]["source"] == "teams-admin-lint"


async def test_submit_remove_job_carries_payload(env):
    settings, records = env
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/admin/jobs", json={"job_type": "remove", "payload": {"path": "wiki/sources/foo.md"}}
        )
        assert resp.status == 202
    assert records[0]["job_type"] == "remove"
    assert records[0]["payload"]["path"] == "wiki/sources/foo.md"


async def test_unknown_job_type_is_400(env):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/jobs", json={"job_type": "frobnicate"})
        assert resp.status == 400


async def test_second_admin_job_409_when_one_is_fresh(env):
    settings, records = env
    FileProgressReporter(settings.admin_job_progress_path, job_id="live", job_type="lint").start()
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/jobs", json={"job_type": "clean"})
        assert resp.status == 409
        assert (await resp.json())["status"] == "already_running"
    assert records == []


async def test_remove_409_when_a_sync_is_running(env):
    settings, records = env
    FileProgressReporter(settings.sync_progress_path, job_id="sync1", job_type="manual").start()
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/jobs", json={"job_type": "remove", "payload": {"path": "wiki/x.md"}})
        assert resp.status == 409
        assert (await resp.json())["status"] == "sync_running"
    assert records == []


# --- /admin/sync/cancel --------------------------------------------------------

async def test_cancel_404_when_no_sync(env):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/sync/cancel", json={})
        assert resp.status == 404


async def test_cancel_running_sync_writes_sentinel(env):
    settings, _ = env
    FileProgressReporter(settings.sync_progress_path, job_id="job-42", job_type="manual").start()
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/sync/cancel", json={"requested_by_user_name": "Dana"})
        assert resp.status == 202
        assert (await resp.json())["status"] == "cancel_requested"
    sentinel = read_cancel(settings.sync_cancel_path)
    assert sentinel and sentinel["job_id"] == "job-42"


async def test_cancel_stale_sync_marks_cancelled_in_place(env):
    settings, _ = env
    stale = {
        "job_id": "old",
        "status": "running",
        "phase": "processing",
        "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    settings.sync_progress_path.write_text(json.dumps(stale), encoding="utf-8")
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/sync/cancel", json={})
        assert resp.status == 200
        assert (await resp.json())["status"] == "cancelled_stale"
    record = json.loads(settings.sync_progress_path.read_text())
    assert record["status"] == "cancelled"


async def test_status_merges_cancel_requested_flag(env):
    settings, _ = env
    FileProgressReporter(settings.sync_progress_path, job_id="job-7", job_type="manual").start()
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        await client.post("/admin/sync/cancel", json={})
        status = await (await client.get("/admin/sync/status")).json()
        assert status["cancel_requested"] is True
