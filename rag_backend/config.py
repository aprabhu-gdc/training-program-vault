"""Compatibility wrapper for shared wiki core settings."""

from packages.wiki_core.settings import (
    DEFAULT_LOCAL_DATA_ROOT,
    IMPLEMENTED_CHAT_PROVIDERS,
    IMPLEMENTED_EMBEDDING_PROVIDERS,
    KNOWN_LLM_PROVIDERS,
    REPO_ROOT,
    CoreSettings,
)


BackendSettings = CoreSettings


__all__ = [
    "BackendSettings",
    "CoreSettings",
    "DEFAULT_LOCAL_DATA_ROOT",
    "IMPLEMENTED_CHAT_PROVIDERS",
    "IMPLEMENTED_EMBEDDING_PROVIDERS",
    "KNOWN_LLM_PROVIDERS",
    "REPO_ROOT",
]
