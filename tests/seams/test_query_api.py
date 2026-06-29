"""Phase 09: wiki query API HTTP contract (QueryService mocked)."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

import apps.wiki_query_api.app as query_app
from packages.contracts.query import Citation, QueryResponse
from packages.wiki_core.retrieval.query_service import QueryService
from packages.wiki_core.settings import CoreSettings
from tests.conftest import make_core_settings


CANNED = QueryResponse(
    answer_text="Start by reviewing the checklist. [Source: PM Checklist]",
    citations=(Citation(title="PM Checklist", path="wiki/sources/pm.md", section="Overview", sources=("raw/sources/pm.docx",)),),
    warnings=(),
    retrieval_diagnostics={"top_k": 6, "chunk_ids": ["c1"]},
)


@pytest.fixture
def offline_env(monkeypatch, tmp_path):
    offline = make_core_settings(tmp_path)
    monkeypatch.setattr(CoreSettings, "from_env", classmethod(lambda cls: offline))
    return offline


async def test_healthcheck_ok(offline_env):
    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.json())["status"] == "ok"


async def test_readyz_503_when_index_unavailable(offline_env):
    # Fresh tmp LanceDB has no table => not ready.
    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/readyz")
        assert resp.status == 503
        assert (await resp.json())["reason"] == "index-unavailable"


async def test_query_success(offline_env, monkeypatch):
    async def fake_query(self, request):
        return CANNED

    monkeypatch.setattr(QueryService, "query", fake_query)

    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/query", json={"query": "How do I start a job?", "request_id": "r1"})
        assert resp.status == 200
        body = await resp.json()
        assert body["answer"] == CANNED.answer_text
        assert body["citations"][0]["title"] == "PM Checklist"
        assert body["citations"][0]["sources"] == ["raw/sources/pm.docx"]
        assert body["retrieval_diagnostics"]["top_k"] == 6


async def test_query_missing_query_field_is_400(offline_env):
    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/query", json={"query": "   "})
        assert resp.status == 400
        assert "query" in (await resp.json())["error"].lower()


async def test_query_wrong_content_type_is_415(offline_env):
    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/query", data="hello", headers={"Content-Type": "text/plain"})
        assert resp.status == 415


async def test_query_index_not_ready_surfaces_503(offline_env, monkeypatch):
    async def raising_query(self, request):
        raise RuntimeError("Wiki index is not ready.")

    monkeypatch.setattr(QueryService, "query", raising_query)

    app = query_app.create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/query", json={"query": "anything"})
        assert resp.status == 503
