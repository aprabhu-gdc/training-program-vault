"""Phase 09: ingest API webhook handshake, job queueing, and manual sync.

Azure Service Bus is mocked (send_json_message recorder) and the SharePoint
adapter's webhook parsing is stubbed, so no Graph/Service Bus calls happen.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

import apps.ingest_api.app as ingest_app
from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.settings import CoreSettings
from tests.conftest import make_core_settings


@pytest.fixture
def queued(monkeypatch, tmp_path):
    """Patch env + Service Bus + CoreSettings; return a list of queued payloads."""
    monkeypatch.setenv("SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://test/;SharedAccessKeyName=k;SharedAccessKey=v")
    monkeypatch.setenv("INGEST_QUEUE_NAME", "test-queue")
    monkeypatch.delenv("SERVICE_BUS_NAMESPACE", raising=False)

    offline = make_core_settings(tmp_path)
    monkeypatch.setattr(CoreSettings, "from_env", classmethod(lambda cls: offline))

    records: list[dict] = []

    def fake_send(*, connection_string, fully_qualified_namespace, queue_name, payload, message_id):
        records.append(payload)

    monkeypatch.setattr(ingest_app, "send_json_message", fake_send)
    return records


async def test_validation_handshake_echoes_token(queued):
    # Microsoft Graph sends the subscription validation request as a POST with a
    # validationToken query parameter and expects a plaintext echo within 10s.
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/webhooks/sharepoint", params={"validationToken": "tok-123"})
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/plain")
        assert await resp.text() == "tok-123"
    assert queued == []  # handshake must not queue anything


async def test_healthcheck_ok(queued):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.json())["status"] == "ok"


async def test_webhook_queues_one_job_per_in_scope_file(queued, monkeypatch):
    events = [
        SourceFileEvent(path="raw/sources/a.docx", event_type="webhook", modified_at="t", entry_id="e1"),
        SourceFileEvent(path="raw/other/b.docx", event_type="webhook"),       # out of scope
        SourceFileEvent(path="raw/sources/c.bin", event_type="webhook"),       # unsupported ext
    ]
    monkeypatch.setattr(SharePointSourceSyncAdapter, "parse_webhook_payload", lambda self, payload: events)

    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/webhooks/sharepoint", json={"value": [{"resourceData": {"id": "x"}}]})
        assert resp.status == 202

    assert len(queued) == 1
    job = queued[0]
    assert job["job_type"] == "webhook"
    assert job["source"] == "sharepoint-webhook"
    assert job["payload"]["path"] == "raw/sources/a.docx"
    assert job["payload"]["modified_at"] == "t"
    assert job["payload"]["entry_id"] == "e1"


async def test_webhook_malformed_json_is_400(queued):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/webhooks/sharepoint", data="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400


async def test_admin_sync_queues_manual_job(queued):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/sync", json={"requested_by_user_name": "Dana"})
        assert resp.status == 202
        body = await resp.json()
        assert body["status"] == "accepted"
        assert body["job_id"]

    assert len(queued) == 1
    assert queued[0]["job_type"] == "manual"
    assert queued[0]["source"] == "teams-manual-sync"
    assert queued[0]["requested_by_user_name"] == "Dana"


async def test_admin_sync_rejects_non_json(queued):
    app = ingest_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/admin/sync", data="x", headers={"Content-Type": "text/plain"})
        assert resp.status == 415
