"""Phase 09: rag_backend.llm is OpenAI/Azure-only after the provider strip."""

from __future__ import annotations

import inspect

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI, AzureOpenAI, OpenAI

import rag_backend.llm as llm
from tests.conftest import make_core_settings


def test_expected_entry_points_exist():
    for name in (
        "create_sync_client",
        "create_async_client",
        "embed_texts_sync",
        "embed_texts_async",
        "complete_json_sync",
        "complete_text_async",
    ):
        assert hasattr(llm, name), f"missing entry point {name}"


@pytest.mark.parametrize("forbidden", ["anthropic", "google", "gemini", "vertex"])
def test_no_legacy_provider_references_in_source(forbidden):
    source = inspect.getsource(llm).lower()
    assert forbidden not in source, f"rag_backend.llm still references {forbidden!r}"


def test_create_sync_client_returns_openai(tmp_path):
    s = make_core_settings(tmp_path, llm_provider="openai")
    client = llm.create_sync_client(s, provider="openai")
    assert isinstance(client, OpenAI)


def test_create_sync_client_returns_azure(tmp_path):
    s = make_core_settings(
        tmp_path,
        llm_provider="azure-openai",
        llm_azure_openai_endpoint="https://example.openai.azure.com",
        llm_azure_openai_api_key="azkey",
        llm_azure_openai_api_version="2024-02-01",
    )
    client = llm.create_sync_client(s, provider="azure-openai")
    assert isinstance(client, AzureOpenAI)


def test_create_async_client_returns_async_types(tmp_path):
    s = make_core_settings(
        tmp_path,
        llm_azure_openai_endpoint="https://example.openai.azure.com",
        llm_azure_openai_api_key="azkey",
    )
    assert isinstance(llm.create_async_client(s, provider="openai"), AsyncOpenAI)
    assert isinstance(llm.create_async_client(s, provider="azure-openai"), AsyncAzureOpenAI)


@pytest.mark.parametrize("provider", ["anthropic", "google", "bedrock"])
def test_create_client_rejects_unsupported_providers(tmp_path, provider):
    s = make_core_settings(tmp_path)
    with pytest.raises(ValueError):
        llm.create_sync_client(s, provider=provider)
    with pytest.raises(ValueError):
        llm.create_async_client(s, provider=provider)
