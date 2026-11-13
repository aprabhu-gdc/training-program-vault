"""ASGI entrypoint for the Claude analytics connector.

Run with:  uvicorn apps.analytics_mcp.app:create_app --factory --host 0.0.0.0 --port 8000

The MCP endpoint is served at ``/mcp``; ``/healthz`` is an unauthenticated
liveness probe that reveals nothing about the data.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from apps.analytics_mcp.config import ConnectorSettings
from apps.analytics_mcp.identity import Caller
from apps.analytics_mcp.server import CachedListSource, build_server, default_caller_resolver


LOGGER = logging.getLogger("apps.analytics_mcp")


def build_auth(settings: ConnectorSettings) -> Any:
    """Entra ID sign-in via FastMCP's OAuth proxy.

    Claude registers itself with the proxy; the proxy sends the person to
    Microsoft sign-in with this app registration and keeps their Entra tokens
    (encrypted) in Azure Table Storage so sessions survive scale-to-zero.
    """

    from fastmcp.server.auth.providers.azure import AzureProvider

    storage = None
    if settings.state_connection_string:
        from key_value.aio.stores.azure_tables import AzureTablesSanitizationStrategy, AzureTablesStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        storage = FernetEncryptionWrapper(
            AzureTablesStore(
                connection_string=settings.state_connection_string,
                table_name=settings.state_table,
                collection_sanitization_strategy=AzureTablesSanitizationStrategy(),
                key_sanitization_strategy=AzureTablesSanitizationStrategy(),
            ),
            source_material=settings.jwt_signing_key,
            salt="graydaze-analytics-mcp-state",
        )

    return AzureProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        tenant_id=settings.tenant_id,
        required_scopes=[settings.scope],
        base_url=settings.base_url,
        allowed_client_redirect_uris=list(settings.allowed_redirect_uris),
        client_storage=storage,
        jwt_signing_key=settings.jwt_signing_key,
        require_authorization_consent=True,
    )


def create_app() -> Any:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from packages.wiki_core.analytics.sharepoint_lists import SharePointListClient
    from packages.wiki_core.settings import CoreSettings
    from teams_bot.services.analytics import AnalyticsService

    settings = ConnectorSettings.from_env()
    for warning in settings.validate():
        LOGGER.warning(warning)

    core = CoreSettings.from_env()
    # One shared Graph client for writes and reads, with list ids pinned when
    # configured (required for list-level Lists.SelectedOperations.Selected).
    lists = SharePointListClient(core)
    lists.set_list_id(core.analytics_query_list_name, settings.query_list_id)
    lists.set_list_id(core.analytics_feedback_list_name, settings.feedback_list_id)
    sink = AnalyticsService(client=lists, settings=core)
    source = CachedListSource(
        lists,
        core.analytics_query_list_name,
        core.analytics_feedback_list_name,
        ttl_seconds=settings.report_cache_seconds,
    )

    if settings.dev_no_auth:
        dev_caller = Caller("local-dev-user", "Local Dev", frozenset({settings.admin_role}))
        auth = None
        resolver = lambda: dev_caller  # noqa: E731
    else:
        auth = build_auth(settings)
        resolver = default_caller_resolver(settings.tenant_id)

    server = build_server(
        sink=sink,
        source=source,
        caller_resolver=resolver,
        admin_role=settings.admin_role,
        auth=auth,
        record_limit_per_minute=settings.record_limit_per_minute,
    )

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    LOGGER.info("Analytics connector ready at %s/mcp", settings.base_url)
    return server.http_app(path="/mcp")
