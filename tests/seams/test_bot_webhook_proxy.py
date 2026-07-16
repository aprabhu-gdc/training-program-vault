"""The bot's public SharePoint webhook proxy (upstream ingest API faked)."""

from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app import MAX_WEBHOOK_BODY_BYTES, make_sharepoint_webhook_proxy


async def _proxy_client(upstream_app: web.Application | None):
    """Return (client, upstream_server) with the proxy pointed at the upstream.

    upstream_app=None yields a proxy pointed at an unreachable port.
    """

    if upstream_app is None:
        target = "http://127.0.0.1:1"  # nothing listens here
        upstream_server = None
    else:
        upstream_server = TestServer(upstream_app)
        await upstream_server.start_server()
        target = str(upstream_server.make_url(""))

    proxy_app = web.Application()
    proxy_app.router.add_post("/api/webhooks/sharepoint", make_sharepoint_webhook_proxy(target))
    client = TestClient(TestServer(proxy_app))
    await client.start_server()
    return client, upstream_server


def _upstream_recorder(records: list):
    async def handler(request: web.Request) -> web.Response:
        token = request.query.get("validationToken")
        if token is not None:
            return web.Response(text=token, content_type="text/plain", status=200)
        records.append(
            {
                "body": await request.read(),
                "content_type": request.headers.get("Content-Type"),
            }
        )
        return web.Response(status=202)

    app = web.Application()
    app.router.add_post("/api/webhooks/sharepoint", handler)
    return app


async def test_validation_token_echo_passes_through():
    client, upstream = await _proxy_client(_upstream_recorder([]))
    try:
        resp = await client.post("/api/webhooks/sharepoint", params={"validationToken": "tok-9"})
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/plain")
        assert await resp.text() == "tok-9"
    finally:
        await client.close()
        await upstream.close()


async def test_body_and_content_type_forwarded_verbatim():
    records: list = []
    client, upstream = await _proxy_client(_upstream_recorder(records))
    try:
        resp = await client.post(
            "/api/webhooks/sharepoint",
            data=b'{"value": []}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 202
        assert records == [{"body": b'{"value": []}', "content_type": "application/json"}]
    finally:
        await client.close()
        await upstream.close()


async def test_unreachable_ingest_api_returns_502():
    client, _ = await _proxy_client(None)
    try:
        resp = await client.post("/api/webhooks/sharepoint", json={"value": []})
        assert resp.status == 502
    finally:
        await client.close()


async def test_oversized_body_is_rejected_before_forwarding():
    records: list = []
    client, upstream = await _proxy_client(_upstream_recorder(records))
    try:
        resp = await client.post(
            "/api/webhooks/sharepoint",
            data=b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
        )
        assert resp.status == 413
        assert records == []
    finally:
        await client.close()
        await upstream.close()