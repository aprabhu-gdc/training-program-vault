"""Configuration helpers for the Graydaze Teams bot."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv


LOGGER = logging.getLogger(__name__)


# Load a local .env file automatically when present so local development and
# containerized runs are simpler. Environment variables still win normally.
load_dotenv()


def _read_env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable value.

    Multiple names are supported so we can accept both the Bot Framework naming
    convention (``MicrosoftAppId``) and uppercase variants if needed later.
    """

    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the bot process."""

    app_id: str
    app_password: str
    port: int
    wiki_query_callable: str
    wiki_query_http_url: str
    wiki_query_timeout_seconds: float
    welcome_examples: tuple[str, str]

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables."""

        return cls(
            app_id=_read_env("MicrosoftAppId", "MICROSOFT_APP_ID"),
            app_password=_read_env("MicrosoftAppPassword", "MICROSOFT_APP_PASSWORD"),
            port=int(_read_env("PORT", default="3978")),
            wiki_query_callable=_read_env("WIKI_QUERY_CALLABLE"),
            wiki_query_http_url=_read_env("WIKI_QUERY_HTTP_URL"),
            wiki_query_timeout_seconds=float(
                _read_env("WIKI_QUERY_TIMEOUT_SECONDS", default="45")
            ),
            welcome_examples=(
                "What is an ETC and how often should I update it?",
                "What should I do before a dump meeting?",
            ),
        )

    def validate(self) -> None:
        """Validate configuration early so startup fails fast when misconfigured."""

        if not self.wiki_query_callable and not self.wiki_query_http_url:
            raise ValueError(
                "Either WIKI_QUERY_CALLABLE or WIKI_QUERY_HTTP_URL is required. "
                "Use WIKI_QUERY_CALLABLE when the existing backend code is installed in the same runtime, "
                "or WIKI_QUERY_HTTP_URL when the vault/query service runs elsewhere."
            )

        if not self.app_id or not self.app_password:
            LOGGER.warning(
                "MicrosoftAppId/MicrosoftAppPassword are empty. This is only suitable for local "
                "Bot Framework Emulator-style testing and will not work for a real Teams deployment."
            )
