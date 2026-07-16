"""Aiohttp entrypoint for the Graydaze Microsoft Teams bot.

This file intentionally focuses on HTTP/Bot Framework plumbing only:
- host an ``/api/messages`` endpoint for Bot Framework traffic
- create the adapter and bot instances
- route incoming activities into the bot

It does *not* implement any wiki/vector/LLM logic. Instead, the bot loads your
existing wiki query function through a configurable import path.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiohttp import web
from botbuilder.core import (
    ConversationState,
    MemoryStorage,
    TurnContext,
    UserState,
)
from botbuilder.integration.aiohttp import (
    CloudAdapter,
    ConfigurationBotFrameworkAuthentication,
    ConfigurationServiceClientCredentialFactory,
)
from botbuilder.schema import Activity

from teams_bot.bot import GraydazeTrainingBot
from teams_bot.config import Settings
from teams_bot.services.analytics import AnalyticsService
from teams_bot.services.feedback import FeedbackLogger
from teams_bot.services.ingest_admin_client import HttpIngestAdminClient
from teams_bot.services.wiki_query import HttpWikiQueryService, WikiQueryService

from packages.shared.logging import configure_logging


# Configure process-wide logging once at startup (shared LOG_LEVEL handling plus
# Azure SDK noise suppression, consistent across the bot, ingest API, and worker).
configure_logging()
LOGGER = logging.getLogger(__name__)


# Microsoft Graph delivers webhook notifications to the public bot port and
# expects a response within 10 seconds; the upstream ingest handler answers
# instantly (validation echo or enqueue), so an 8s budget leaves headroom to
# still return a real 502 when the ingest API is down.
WEBHOOK_PROXY_TIMEOUT_SECONDS = 8.0
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


def make_sharepoint_webhook_proxy(target_base_url: str):
    """Build a handler that forwards Graph webhook POSTs to the ingest API.

    The ingest API owns the SharePoint webhook logic (validation-token echo,
    clientState check, queueing) but listens on a private localhost port; only
    the bot's port is exposed by App Service. This proxy forwards the request
    verbatim — raw query string (carries ``validationToken``), body, and
    Content-Type — and returns the upstream status/body unchanged. Bodies are
    never logged: notification payloads carry the clientState secret.
    """

    target_url = target_base_url.rstrip("/") + "/api/webhooks/sharepoint"

    async def proxy(request: web.Request) -> web.Response:
        body = await request.read()
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            return web.json_response({"error": "Payload too large."}, status=413)

        url = f"{target_url}?{request.query_string}" if request.query_string else target_url
        headers = {}
        if request.headers.get("Content-Type"):
            headers["Content-Type"] = request.headers["Content-Type"]

        timeout = aiohttp.ClientTimeout(total=WEBHOOK_PROXY_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=body, headers=headers) as upstream:
                    payload = await upstream.read()
                    content_type = (upstream.headers.get("Content-Type") or "application/json").split(";")[0]
                    return web.Response(body=payload, status=upstream.status, content_type=content_type)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.warning("SharePoint webhook proxy could not reach the ingest API at %s", target_url)
            return web.json_response({"error": "Ingest service unavailable."}, status=502)

    return proxy


class _BotAuthConfig:
    """Adapts our ``Settings`` to the attribute names the Bot Framework
    configuration auth classes look up (``APP_ID``/``APP_PASSWORD``/``APP_TYPE``/
    ``APP_TENANTID``)."""

    def __init__(self, settings: Settings) -> None:
        self.APP_ID = settings.app_id
        self.APP_PASSWORD = settings.app_password
        self.APP_TYPE = settings.app_type
        self.APP_TENANTID = settings.app_tenant_id


def create_app() -> web.Application:
    """Create and configure the aiohttp web application.

    The returned app exposes:
    - POST /api/messages  -> Bot Framework endpoint for Teams activities
    - GET  /healthz       -> Lightweight health probe for hosting platforms
    """

    settings = Settings.from_env()
    settings.validate()

    # MemoryStorage keeps the sample easy to run locally. For multi-instance or
    # horizontally scaled deployments, replace this with a shared Bot Framework
    # Storage implementation so welcome-state survives process restarts.
    storage = MemoryStorage()
    conversation_state = ConversationState(storage)
    user_state = UserState(storage)

    if settings.wiki_query_http_url:
        wiki_query_service = HttpWikiQueryService(
            base_url=settings.wiki_query_http_url,
            timeout_seconds=settings.wiki_query_timeout_seconds,
        )
    else:
        wiki_query_service = WikiQueryService.from_import_path(
            settings.wiki_query_callable,
            timeout_seconds=settings.wiki_query_timeout_seconds,
        )
    feedback_logger = FeedbackLogger()
    ingest_admin_client = HttpIngestAdminClient(
        base_url=settings.ingest_admin_http_url,
        timeout_seconds=settings.wiki_query_timeout_seconds,
    )
    # SharePoint-list analytics sink (concept + feedback events for Power BI).
    # Lazy and fail-soft: if SharePoint is unconfigured it disables itself.
    analytics = AnalyticsService()

    bot = GraydazeTrainingBot(
        settings=settings,
        user_state=user_state,
        conversation_state=conversation_state,
        wiki_query_service=wiki_query_service,
        feedback_logger=feedback_logger,
        ingest_admin_client=ingest_admin_client,
        analytics=analytics,
    )

    # CloudAdapter + ConfigurationBotFrameworkAuthentication supports both
    # MultiTenant (default, fine for the Bot Framework Emulator) and SingleTenant
    # (the posture for an internal whole-company bot). The credential factory reads
    # APP_ID / APP_PASSWORD / APP_TYPE / APP_TENANTID off this config shim.
    auth_config = _BotAuthConfig(settings)
    bot_auth = ConfigurationBotFrameworkAuthentication(
        auth_config,
        credentials_factory=ConfigurationServiceClientCredentialFactory(auth_config),
    )
    adapter = CloudAdapter(bot_auth)

    async def on_turn_error(turn_context: TurnContext, error: Exception) -> None:
        """Global catch-all for exceptions that escape the bot logic.

        Users get a short friendly error. Operators get the full stack trace in
        application logs.
        """

        LOGGER.exception("Unhandled bot error", exc_info=error)
        await turn_context.send_activity(
            "Sorry, I hit an unexpected error while talking to the Graydaze PM Training Vault. "
            "Please try again in a moment."
        )

    adapter.on_turn_error = on_turn_error

    async def healthcheck(_: web.Request) -> web.Response:
        """Simple health probe endpoint for Azure/App Service/Kubernetes."""

        return web.json_response({"status": "ok"})

    async def messages(request: web.Request) -> web.Response:
        """Bot Framework activity endpoint.

        This route:
        1. validates content type
        2. deserializes the Bot Framework activity payload
        3. passes the activity into the Bot Framework adapter
        4. returns invoke responses when the adapter produces one
        """

        if request.content_type != "application/json":
            return web.json_response(
                {"error": "Only application/json payloads are supported."},
                status=415,
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            LOGGER.warning("Rejected malformed JSON request on /api/messages")
            return web.json_response({"error": "Malformed JSON payload."}, status=400)

        activity = Activity().deserialize(body)
        auth_header = request.headers.get("Authorization", "")

        try:
            # CloudAdapter.process_activity takes the auth header first, then the
            # activity (the reverse of the legacy BotFrameworkAdapter order).
            invoke_response = await adapter.process_activity(
                auth_header,
                activity,
                bot.on_turn,
            )
        except Exception as exc:  # pragma: no cover - defensive request-level guard
            LOGGER.exception("Adapter failed to process activity", exc_info=exc)
            return web.json_response(
                {"error": "Failed to process bot activity."},
                status=500,
            )

        if invoke_response:
            return web.json_response(invoke_response.body, status=invoke_response.status)

        return web.Response(status=201)

    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_post("/api/messages", messages)
    # Graph webhook notifications arrive on the public bot port and are relayed
    # to the private ingest API, which owns the actual webhook handling.
    app.router.add_post(
        "/api/webhooks/sharepoint",
        make_sharepoint_webhook_proxy(settings.ingest_admin_http_url),
    )
    app["settings"] = settings

    LOGGER.info(
        "Graydaze Teams bot configured on port=%s query_target=%s",
        settings.port,
        settings.wiki_query_http_url or settings.wiki_query_callable,
    )
    return app


if __name__ == "__main__":
    application = create_app()
    settings: Settings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
