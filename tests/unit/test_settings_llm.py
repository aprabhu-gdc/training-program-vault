"""Phase 09: single-provider LLM stack (Anthropic/Google removed) + validation."""

from __future__ import annotations

import pytest

from packages.wiki_core.settings import (
    IMPLEMENTED_CHAT_PROVIDERS,
    IMPLEMENTED_EMBEDDING_PROVIDERS,
    KNOWN_LLM_PROVIDERS,
    _normalize_provider,
)
from tests.conftest import make_core_settings


def test_provider_sets_are_openai_and_azure_only():
    expected = {"openai", "azure-openai"}
    assert KNOWN_LLM_PROVIDERS == expected
    assert IMPLEMENTED_CHAT_PROVIDERS == expected
    assert IMPLEMENTED_EMBEDDING_PROVIDERS == expected


@pytest.mark.parametrize("removed", ["anthropic", "google", "gemini", "claude"])
def test_legacy_providers_are_gone(removed):
    assert removed not in KNOWN_LLM_PROVIDERS
    assert removed not in IMPLEMENTED_CHAT_PROVIDERS
    assert removed not in IMPLEMENTED_EMBEDDING_PROVIDERS


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("azure", "azure-openai"),
        ("azureopenai", "azure-openai"),
        ("Azure-OpenAI", "azure-openai"),
        ("openai", "openai"),
        ("openai-compatible", "openai"),
        ("OPENAI_COMPATIBLE", "openai"),
    ],
)
def test_normalize_provider_aliases(raw, normalized):
    assert _normalize_provider(raw) == normalized


def test_provider_fallback_chains(tmp_path):
    # chat falls back to llm_provider; vision falls back to chat then provider;
    # embedding falls back to provider then chat.
    s = make_core_settings(
        tmp_path,
        llm_provider="openai",
        llm_chat_provider="",
        llm_vision_provider="",
        llm_embedding_provider="",
    )
    assert s.chat_provider == "openai"
    assert s.vision_provider == "openai"
    assert s.embedding_provider == "openai"

    s2 = make_core_settings(
        tmp_path,
        llm_provider="",
        llm_chat_provider="azure",
        llm_vision_provider="",
        llm_embedding_provider="",
    )
    assert s2.chat_provider == "azure-openai"
    assert s2.vision_provider == "azure-openai"  # falls back to chat provider


def test_validate_llm_passes_for_valid_openai(tmp_path):
    s = make_core_settings(tmp_path)  # defaults are a valid openai config
    s.validate_llm()  # must not raise


def test_validate_llm_passes_for_valid_azure(tmp_path):
    s = make_core_settings(
        tmp_path,
        llm_provider="azure-openai",
        llm_openai_api_key="",
        llm_azure_openai_endpoint="https://example.openai.azure.com",
        llm_azure_openai_api_key="azkey",
        llm_azure_openai_api_version="2024-02-01",
    )
    s.validate_llm()  # must not raise


def test_validate_llm_requires_chat_model(tmp_path):
    s = make_core_settings(tmp_path, llm_chat_model="")
    with pytest.raises(ValueError, match="LLM_CHAT_MODEL"):
        s.validate_llm()


def test_validate_llm_requires_embedding_model(tmp_path):
    s = make_core_settings(tmp_path, llm_embedding_model="")
    with pytest.raises(ValueError, match="LLM_EMBEDDING_MODEL"):
        s.validate_llm()


def test_validate_llm_requires_openai_key(tmp_path):
    s = make_core_settings(tmp_path, llm_provider="openai", llm_openai_api_key="")
    with pytest.raises(ValueError, match="LLM_OPENAI_API_KEY"):
        s.validate_llm()


def test_validate_llm_azure_reports_missing_settings(tmp_path):
    s = make_core_settings(
        tmp_path,
        llm_provider="azure-openai",
        llm_openai_api_key="",
        llm_azure_openai_endpoint="",
        llm_azure_openai_api_key="",
        llm_azure_openai_api_version="",
    )
    with pytest.raises(ValueError, match="Azure OpenAI"):
        s.validate_llm()


@pytest.mark.parametrize("bad", ["anthropic", "google"])
def test_validate_llm_rejects_removed_providers(tmp_path, bad):
    s = make_core_settings(tmp_path, llm_provider=bad)
    with pytest.raises(ValueError, match="Unsupported|not implemented"):
        s.validate_llm()


def test_ingest_model_falls_back_to_chat_model_when_unset(tmp_path):
    s = make_core_settings(tmp_path, llm_chat_model="gpt-chat", llm_ingest_model="")
    assert s.resolved_ingest_model == "gpt-chat"


def test_ingest_model_overrides_chat_model_when_set(tmp_path):
    s = make_core_settings(tmp_path, llm_chat_model="gpt-chat", llm_ingest_model="gpt-5.4-mini")
    assert s.resolved_ingest_model == "gpt-5.4-mini"


def test_ingest_provider_falls_back_to_chat_then_llm_provider(tmp_path):
    s = make_core_settings(tmp_path, llm_provider="openai", llm_chat_provider="", llm_ingest_provider="")
    assert s.ingest_provider == "openai"

    s2 = make_core_settings(tmp_path, llm_provider="openai", llm_ingest_provider="azure")
    assert s2.ingest_provider == "azure-openai"


def test_validate_llm_rejects_bad_ingest_provider(tmp_path):
    s = make_core_settings(tmp_path, llm_ingest_provider="anthropic")
    with pytest.raises(ValueError, match="Unsupported|not implemented"):
        s.validate_llm()
