"""Aiohttp app exposing the extracted wiki query service over HTTP."""

from __future__ import annotations

import json
import logging

from aiohttp import web

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import QueryAttachment, QueryRequest
from packages.wiki_core.retrieval.query_service import QueryService

from .config import QueryApiSettings
from packages.shared.logging import configure_logging


configure_logging()
LOGGER = logging.getLogger(__name__)


def _parse_request(body: dict) -> QueryRequest:
    attachments = tuple(
        QueryAttachment(
            name=str(item.get("name") or "attachment"),
            content_type=str(item.get("content_type") or "application/octet-stream"),
            text_content=(str(item.get("text_content")) if item.get("text_content") is not None else None),
            image_data_url=(str(item.get("image_data_url")) if item.get("image_data_url") is not None else None),
            blob_ref=(str(item.get("blob_ref")) if item.get("blob_ref") is not None else None),
        )
        for item in list(body.get("attachments") or [])
        if isinstance(item, dict)
    )

    identity = CallerIdentity(
        user_id=(str(body.get("user_id")) if body.get("user_id") is not None else None),
        user_name=(str(body.get("user_name")) if body.get("user_name") is not None else None),
        tenant_id=(str(body.get("tenant_id")) if body.get("tenant_id") is not None else None),
        client_app=(str(body.get("client_app")) if body.get("client_app") is not None else "wiki-query-api"),
        channel_id=(str(body.get("channel_id")) if body.get("channel_id") is not None else None),
        conversation_id=(str(body.get("conversation_id")) if body.get("conversation_id") is not None else None),
        locale=(str(body.get("locale")) if body.get("locale") is not None else None),
    )

    return QueryRequest(
        request_id=str(body.get("request_id") or "request"),
        query=str(body.get("query") or "").strip(),
        identity=identity,
        attachments=attachments,
        client_context={"channel_data": body.get("channel_data")},
    )


def create_app() -> web.Application:
    settings = QueryApiSettings.from_env()
    query_service = QueryService(settings.backend)

    async def healthcheck(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def readycheck(_: web.Request) -> web.Response:
        if not query_service._vector_store.is_ready():  # noqa: SLF001
            return web.json_response({"status": "not-ready", "reason": "index-unavailable"}, status=503)
        return web.json_response({"status": "ready"})

    async def query(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Only application/json payloads are supported."}, status=415)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            LOGGER.warning("Rejected malformed JSON request on /query")
            return web.json_response({"error": "Malformed JSON payload."}, status=400)

        if not isinstance(body, dict):
            return web.json_response({"error": "Query payload must be a JSON object."}, status=400)

        query_request = _parse_request(body)
        if not query_request.query:
            return web.json_response({"error": "Field 'query' is required."}, status=400)

        try:
            result = await query_service.query(query_request)
        except RuntimeError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Query API request failed", exc_info=exc)
            return web.json_response({"error": "Failed to process query."}, status=500)

        return web.json_response(
            {
                "answer": result.answer_text,
                "citations": [
                    {
                        "title": citation.title,
                        "path": citation.path,
                        "section": citation.section,
                        "sources": list(citation.sources),
                    }
                    for citation in result.citations
                ],
                "retrieval_diagnostics": result.retrieval_diagnostics,
                "warnings": list(result.warnings),
            }
        )

    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_get("/readyz", readycheck)
    app.router.add_post("/query", query)
    app["settings"] = settings
    return app


if __name__ == "__main__":
    application = create_app()
    settings: QueryApiSettings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
