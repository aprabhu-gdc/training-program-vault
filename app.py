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
import os

from aiohttp import web
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    ConversationState,
    MemoryStorage,
    TurnContext,
    UserState,
)
from botbuilder.schema import Activity

from rag_backend.auto_ingest import AutoIngestService
from teams_bot.bot import GraydazeTrainingBot
from teams_bot.config import Settings
from teams_bot.services.feedback import FeedbackLogger
from teams_bot.services.wiki_query import HttpWikiQueryService, WikiQueryService


# Configure process-wide logging once at startup. The format keeps the output
# readable in container logs, Azure App Service logs, or local terminal runs.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)


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
    sync_service: AutoIngestService | None = None

    async def run_sync_job(reason: str, payload: dict | None = None):
        nonlocal sync_service
        LOGGER.info("Starting background sync reason=%s", reason)
        try:
            if sync_service is None:
                sync_service = AutoIngestService(settings.backend)
            if payload is not None:
                return await asyncio.to_thread(sync_service.sync_from_webhook, payload)
            return await asyncio.to_thread(sync_service.sync_all_training_files)
        except Exception:
            LOGGER.exception("Background sync failed reason=%s", reason)
            raise

    def on_background_task_done(task: asyncio.Task) -> None:
        app["background_tasks"].discard(task)
        try:
            task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            LOGGER.debug("Background task raised after completion callback", exc_info=True)

    def schedule_background_sync(reason: str, payload: dict | None = None) -> asyncio.Task:
        task = asyncio.create_task(run_sync_job(reason, payload))
        task.add_done_callback(on_background_task_done)
        app["background_tasks"].add(task)
        return task

    bot = GraydazeTrainingBot(
        settings=settings,
        user_state=user_state,
        conversation_state=conversation_state,
        wiki_query_service=wiki_query_service,
        feedback_logger=feedback_logger,
        sync_runner=run_sync_job,
    )

    adapter_settings = BotFrameworkAdapterSettings(
        settings.app_id,
        settings.app_password,
    )
    adapter = BotFrameworkAdapter(adapter_settings)

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
            invoke_response = await adapter.process_activity(
                activity,
                auth_header,
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

    async def egnyte_webhook(request: web.Request) -> web.Response:
        """Accept Egnyte change notifications and trigger background ingest."""

        if request.content_type != "application/json":
            return web.json_response(
                {"error": "Only application/json payloads are supported."},
                status=415,
            )

        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            LOGGER.warning("Rejected malformed JSON request on /api/webhooks/egnyte")
            return web.json_response({"error": "Malformed JSON payload."}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"error": "Webhook payload must be a JSON object."}, status=400)

        schedule_background_sync("egnyte-webhook", payload)
        return web.json_response({"status": "accepted"}, status=202)

    async def on_startup(app: web.Application) -> None:
        app["background_tasks"] = set()

    async def on_shutdown(app: web.Application) -> None:
        tasks = list(app.get("background_tasks", set()))
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/healthz", healthcheck)
    app.router.add_post("/api/messages", messages)
    app.router.add_post("/api/webhooks/egnyte", egnyte_webhook)
    app["settings"] = settings

    LOGGER.info(
        "Graydaze Teams bot configured on port=%s query_callable=%s",
        settings.port,
        settings.wiki_query_http_url or settings.wiki_query_callable,
    )
    return app


if __name__ == "__main__":
    application = create_app()
    settings: Settings = application["settings"]
    web.run_app(application, host="0.0.0.0", port=settings.port)
