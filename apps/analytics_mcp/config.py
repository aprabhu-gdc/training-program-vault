"""Environment configuration for the Claude analytics connector.

SharePoint list access reuses the existing ``SHAREPOINT_*`` settings read by
``packages.wiki_core.settings.CoreSettings``; everything connector-specific is
prefixed ``ANALYTICS_MCP_``. See docs/claude-analytics-connector.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv


# Claude's OAuth callback(s). Only these clients may complete sign-in through
# the connector, so another app cannot use it to obtain tokens for your users.
DEFAULT_ALLOWED_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)

MIN_SIGNING_KEY_CHARS = 32
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ConnectorSettings:
    base_url: str
    tenant_id: str
    client_id: str
    client_secret: str
    jwt_signing_key: str
    scope: str
    admin_role: str
    allowed_redirect_uris: tuple[str, ...]
    state_connection_string: str
    state_table: str
    query_list_id: str
    feedback_list_id: str
    report_cache_seconds: float
    record_limit_per_minute: int
    dev_no_auth: bool

    @classmethod
    def from_env(cls) -> "ConnectorSettings":
        load_dotenv(override=False)
        redirects = tuple(
            uri.strip()
            for uri in _env("ANALYTICS_MCP_ALLOWED_REDIRECT_URIS").split(",")
            if uri.strip()
        ) or DEFAULT_ALLOWED_REDIRECT_URIS
        return cls(
            base_url=_env("ANALYTICS_MCP_BASE_URL").rstrip("/"),
            tenant_id=_env("ANALYTICS_MCP_TENANT_ID") or _env("SHAREPOINT_TENANT_ID"),
            client_id=_env("ANALYTICS_MCP_CLIENT_ID"),
            client_secret=_env("ANALYTICS_MCP_CLIENT_SECRET"),
            jwt_signing_key=_env("ANALYTICS_MCP_JWT_SIGNING_KEY"),
            scope=_env("ANALYTICS_MCP_SCOPE", "analytics.use"),
            admin_role=_env("ANALYTICS_MCP_ADMIN_ROLE", "Analytics.Admin"),
            allowed_redirect_uris=redirects,
            state_connection_string=_env("ANALYTICS_MCP_STATE_CONNECTION_STRING"),
            state_table=_env("ANALYTICS_MCP_STATE_TABLE", "mcpoauthstate"),
            query_list_id=_env("ANALYTICS_MCP_QUERY_LIST_ID"),
            feedback_list_id=_env("ANALYTICS_MCP_FEEDBACK_LIST_ID"),
            report_cache_seconds=float(_env("ANALYTICS_MCP_REPORT_CACHE_SECONDS", "60")),
            record_limit_per_minute=int(_env("ANALYTICS_MCP_RECORD_LIMIT_PER_MINUTE", "30")),
            dev_no_auth=_env_bool("ANALYTICS_MCP_DEV_NO_AUTH"),
        )

    @property
    def is_local(self) -> bool:
        return (urlparse(self.base_url).hostname or "") in LOCAL_HOSTS

    def validate(self) -> list[str]:
        """Raise on unsafe or missing configuration; return non-fatal warnings."""

        if not self.base_url:
            raise ValueError("ANALYTICS_MCP_BASE_URL is required (the connector's public URL).")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" and not self.is_local:
            raise ValueError("ANALYTICS_MCP_BASE_URL must be https outside local development.")

        if self.dev_no_auth:
            if not self.is_local:
                raise ValueError(
                    "ANALYTICS_MCP_DEV_NO_AUTH is only allowed when ANALYTICS_MCP_BASE_URL is "
                    "localhost. Refusing to run an unauthenticated connector on a public URL."
                )
            return ["Running WITHOUT sign-in (ANALYTICS_MCP_DEV_NO_AUTH). Local development only."]

        missing = [
            name
            for name, value in (
                ("ANALYTICS_MCP_TENANT_ID (or SHAREPOINT_TENANT_ID)", self.tenant_id),
                ("ANALYTICS_MCP_CLIENT_ID", self.client_id),
                ("ANALYTICS_MCP_CLIENT_SECRET", self.client_secret),
                ("ANALYTICS_MCP_JWT_SIGNING_KEY", self.jwt_signing_key),
            )
            if not value
        ]
        if missing:
            raise ValueError("Analytics connector requires: " + ", ".join(missing))
        if len(self.jwt_signing_key) < MIN_SIGNING_KEY_CHARS:
            raise ValueError(
                f"ANALYTICS_MCP_JWT_SIGNING_KEY must be at least {MIN_SIGNING_KEY_CHARS} characters "
                "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
            )

        warnings = []
        if not self.state_connection_string:
            if not self.is_local:
                raise ValueError(
                    "ANALYTICS_MCP_STATE_CONNECTION_STRING is required in production: without it, "
                    "sign-in sessions are kept on the container's disk and everyone is signed out "
                    "whenever it scales to zero."
                )
            warnings.append("No ANALYTICS_MCP_STATE_CONNECTION_STRING: sign-in state is kept on local disk.")
        return warnings
