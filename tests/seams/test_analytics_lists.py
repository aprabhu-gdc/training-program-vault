"""SharePointListClient Graph plumbing (transport faked, fully offline)."""

from __future__ import annotations

import json

import httpx
import pytest

from packages.wiki_core.analytics.sharepoint_lists import (
    FEEDBACK_COLUMNS,
    QUERY_EVENT_COLUMNS,
    SharePointListClient,
)
from tests.conftest import make_core_settings


class GraphFake:
    """Minimal Graph + token endpoint double behind httpx.MockTransport."""

    def __init__(self):
        self.token_requests = 0
        self.list_queries = 0
        self.items: list[tuple[str, dict, str]] = []  # (url, body, bearer)
        self.created_lists: list[dict] = []
        self.lists = {"TrainingBotQueryEvents": "list-q", "TrainingBotFeedback": "list-f"}
        self.fail_next_items_post_with: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            self.token_requests += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{self.token_requests}", "expires_in": 3600}
            )
        if request.method == "GET" and "/lists?" in url:
            self.list_queries += 1
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": list_id, "displayName": name}
                        for name, list_id in self.lists.items()
                    ]
                },
            )
        if request.method == "POST" and url.endswith("/items"):
            if self.fail_next_items_post_with is not None:
                status = self.fail_next_items_post_with
                self.fail_next_items_post_with = None
                return httpx.Response(status, json={"error": {"code": str(status)}})
            self.items.append(
                (url, json.loads(request.content), request.headers.get("Authorization", ""))
            )
            return httpx.Response(201, json={"id": "1"})
        if request.method == "POST" and url.endswith("/lists"):
            body = json.loads(request.content)
            self.created_lists.append(body)
            self.lists[body["displayName"]] = "list-new"
            return httpx.Response(201, json={"id": "list-new"})
        if request.method == "GET" and "contoso.sharepoint.com:" in url:
            return httpx.Response(200, json={"id": "resolved-site-id"})
        return httpx.Response(404, json={"error": {"code": "notFound"}})


@pytest.fixture
def graph(monkeypatch):
    fake = GraphFake()
    transport = httpx.MockTransport(fake.handler)
    real_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client)
    return fake


def _client(tmp_path, **overrides) -> SharePointListClient:
    return SharePointListClient(make_core_settings(tmp_path, **overrides))


def test_create_item_posts_fields_object_with_bearer_token(graph, tmp_path):
    client = _client(tmp_path)
    fields = {"Title": "Estimate to Complete", "Concept": "Estimate to Complete"}
    client.create_item("TrainingBotQueryEvents", fields)

    url, body, bearer = graph.items[0]
    assert "/sites/site-test-id/lists/list-q/items" in url
    assert body == {"fields": fields}
    assert bearer == "Bearer tok-1"


def test_list_id_and_token_are_cached_across_writes(graph, tmp_path):
    client = _client(tmp_path)
    client.create_item("TrainingBotQueryEvents", {"Title": "a"})
    client.create_item("TrainingBotQueryEvents", {"Title": "b"})

    assert len(graph.items) == 2
    assert graph.list_queries == 1
    assert graph.token_requests == 1


def test_401_refreshes_token_and_retries_once(graph, tmp_path):
    client = _client(tmp_path)
    graph.fail_next_items_post_with = 401
    client.create_item("TrainingBotQueryEvents", {"Title": "a"})

    assert len(graph.items) == 1
    assert graph.token_requests == 2
    assert graph.items[0][2] == "Bearer tok-2"


def test_non_auth_error_raises_for_caller_to_swallow(graph, tmp_path):
    client = _client(tmp_path)
    graph.fail_next_items_post_with = 429
    with pytest.raises(httpx.HTTPStatusError):
        client.create_item("TrainingBotQueryEvents", {"Title": "a"})


def test_ensure_list_is_idempotent(graph, tmp_path):
    client = _client(tmp_path)
    assert client.ensure_list("TrainingBotQueryEvents", QUERY_EVENT_COLUMNS) is False
    assert graph.created_lists == []

    assert client.ensure_list("TrainingBotQueryEvents-Test", QUERY_EVENT_COLUMNS) is True
    created = graph.created_lists[0]
    assert created["displayName"] == "TrainingBotQueryEvents-Test"
    assert created["list"] == {"template": "genericList"}
    assert [column["name"] for column in created["columns"]] == [
        column["name"] for column in QUERY_EVENT_COLUMNS
    ]


def test_missing_list_raises_with_setup_hint(graph, tmp_path):
    client = _client(tmp_path)
    with pytest.raises(ValueError, match="setup_analytics_lists"):
        client.create_item("NoSuchList", {"Title": "a"})


def test_site_id_resolved_from_hostname_when_not_configured(graph, tmp_path):
    client = _client(
        tmp_path,
        sharepoint_site_id="",
        sharepoint_site_hostname="contoso.sharepoint.com",
        sharepoint_site_path="/sites/graydaze",
    )
    client.create_item("TrainingBotQueryEvents", {"Title": "a"})
    assert "/sites/resolved-site-id/" in graph.items[0][0]


def test_validation_requires_auth_and_site(tmp_path):
    with pytest.raises(ValueError, match="SHAREPOINT_TENANT_ID"):
        _client(tmp_path, sharepoint_tenant_id="")
    with pytest.raises(ValueError, match="SHAREPOINT_SITE_ID"):
        _client(tmp_path, sharepoint_site_id="", sharepoint_site_hostname="")


def test_feedback_columns_cover_comment_and_concepts():
    names = {column["name"] for column in FEEDBACK_COLUMNS}
    assert {"Rating", "Comment", "Concepts"} <= names
