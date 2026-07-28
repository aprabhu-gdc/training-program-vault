"""App-only token lifecycle for the sync adapter (transport faked, fully offline).

Regression cover for the `/sync` failure where a long-lived adapter cached its
Graph token forever: every call more than ~1h after the worker started failed
with `401 Unauthorized` on the first request of the job
(`/drives/{id}/root:/wiki:/children`).
"""

from __future__ import annotations

import httpx
import pytest

from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from tests.conftest import make_core_settings


class GraphFake:
    """Token endpoint + drive-children endpoint behind httpx.MockTransport."""

    def __init__(self, *, unauthorized_children_calls: int = 0, expires_in: int = 3600):
        self.token_requests = 0
        self.expires_in = expires_in
        self.unauthorized_children_calls = unauthorized_children_calls
        self.children_bearers: list[str] = []
        self.download_bearers: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        bearer = request.headers.get("Authorization", "")

        if "login.microsoftonline.com" in url:
            self.token_requests += 1
            return httpx.Response(
                200,
                json={"access_token": f"tok-{self.token_requests}", "expires_in": self.expires_in},
            )

        if url.endswith(":/children"):
            self.children_bearers.append(bearer)
            if self.unauthorized_children_calls > 0:
                self.unauthorized_children_calls -= 1
                return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "page-1",
                            "name": "topic.md",
                            "file": {"mimeType": "text/markdown"},
                            "parentReference": {"path": "/drives/drive-test-id/root:/wiki"},
                        }
                    ]
                },
            )

        if url.endswith(":/content"):
            self.download_bearers.append(bearer)
            if self.unauthorized_children_calls > 0:
                self.unauthorized_children_calls -= 1
                return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
            return httpx.Response(200, content=b"# page")

        return httpx.Response(404, json={"error": {"code": "notFound"}})


@pytest.fixture
def graph_factory(monkeypatch):
    def build(**kwargs) -> GraphFake:
        fake = GraphFake(**kwargs)
        transport = httpx.MockTransport(fake.handler)
        real_client = httpx.Client

        def patched_client(*args, **client_kwargs):
            client_kwargs["transport"] = transport
            return real_client(*args, **client_kwargs)

        monkeypatch.setattr(httpx, "Client", patched_client)
        return fake

    return build


def _adapter(tmp_path) -> SharePointSourceSyncAdapter:
    return SharePointSourceSyncAdapter(make_core_settings(tmp_path))


def test_valid_token_is_reused_across_calls(graph_factory, tmp_path):
    graph = graph_factory()
    adapter = _adapter(tmp_path)

    adapter.list_files_recursive("wiki")
    adapter.list_files_recursive("wiki")

    assert graph.token_requests == 1
    assert graph.children_bearers == ["Bearer tok-1", "Bearer tok-1"]


def test_expired_token_is_refreshed_before_the_next_call(graph_factory, tmp_path):
    """The bug: the worker holds one adapter for days, so expiry must be tracked."""
    graph = graph_factory()
    adapter = _adapter(tmp_path)

    adapter.list_files_recursive("wiki")
    adapter._token_expires_at = 0.0  # noqa: SLF001 -- simulate the clock passing the expiry
    adapter.list_files_recursive("wiki")

    assert graph.token_requests == 2
    assert graph.children_bearers == ["Bearer tok-1", "Bearer tok-2"]


def test_short_lived_token_refreshes_without_waiting_for_a_401(graph_factory, tmp_path):
    """A token that expires inside the buffer window is never reused."""
    graph = graph_factory(expires_in=60)
    adapter = _adapter(tmp_path)

    adapter.list_files_recursive("wiki")
    assert adapter._token_expires_at > 0.0  # noqa: SLF001 -- floor keeps it usable, not zero


def test_401_refreshes_the_token_and_retries_once(graph_factory, tmp_path):
    graph = graph_factory(unauthorized_children_calls=1)
    adapter = _adapter(tmp_path)

    events = adapter.list_files_recursive("wiki")

    assert [event.path for event in events] == ["wiki/topic.md"]
    assert graph.token_requests == 2
    # The retry carried the *new* token, not the rejected one.
    assert graph.children_bearers == ["Bearer tok-1", "Bearer tok-2"]


def test_persistent_401_raises_after_a_single_retry(graph_factory, tmp_path):
    graph = graph_factory(unauthorized_children_calls=99)
    adapter = _adapter(tmp_path)

    with pytest.raises(httpx.HTTPStatusError):
        adapter.list_files_recursive("wiki")

    assert graph.token_requests == 2  # one refresh, then fatal
    assert len(graph.children_bearers) == 2


def test_download_refreshes_the_token_and_retries_once(graph_factory, tmp_path):
    graph = graph_factory(unauthorized_children_calls=1)
    adapter = _adapter(tmp_path)
    destination = tmp_path / "wiki" / "topic.md"

    adapter.download_remote_file("wiki/topic.md", destination)

    assert destination.read_bytes() == b"# page"
    assert graph.token_requests == 2
    assert graph.download_bearers == ["Bearer tok-1", "Bearer tok-2"]
