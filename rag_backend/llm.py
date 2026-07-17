"""LLM helpers for retrieval and ingestion.

The configuration contract is provider-agnostic. The runtime implementation in
this module supports:

- openai
- azure-openai
"""

from __future__ import annotations

import json
from itertools import islice
from typing import Any, Iterable, Sequence

from openai import AsyncAzureOpenAI, AsyncOpenAI, AzureOpenAI, OpenAI

from rag_backend.config import BackendSettings


def _batched(values: Sequence[str], size: int = 64) -> Iterable[list[str]]:
    iterator = iter(values)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def _chat_model(settings: BackendSettings, *, requires_vision: bool = False) -> str:
    return settings.resolved_vision_model if requires_vision else settings.resolved_chat_model


def _embedding_model(settings: BackendSettings) -> str:
    return settings.resolved_embedding_model


# The SDK default of 2 retries is not enough for batch workloads (index builds,
# full-vault ingest): the Azure S0 tier rate-limits embeddings aggressively and a
# surfaced 429 used to abort an entire sync. Retries honor the service retry-after.
_MAX_RETRIES = 6


def create_sync_client(settings: BackendSettings, *, provider: str) -> OpenAI | AzureOpenAI:
    if provider == "azure-openai":
        return AzureOpenAI(
            api_key=settings.llm_azure_openai_api_key,
            azure_endpoint=settings.llm_azure_openai_endpoint,
            api_version=settings.llm_azure_openai_api_version,
            max_retries=_MAX_RETRIES,
        )

    if provider == "openai":
        kwargs = {"api_key": settings.llm_openai_api_key, "max_retries": _MAX_RETRIES}
        if settings.llm_openai_base_url:
            kwargs["base_url"] = settings.llm_openai_base_url
        return OpenAI(**kwargs)

    raise ValueError(
        "OpenAI SDK client creation is only supported for providers 'openai' and 'azure-openai'."
    )


def create_async_client(settings: BackendSettings, *, provider: str) -> AsyncOpenAI | AsyncAzureOpenAI:
    if provider == "azure-openai":
        return AsyncAzureOpenAI(
            api_key=settings.llm_azure_openai_api_key,
            azure_endpoint=settings.llm_azure_openai_endpoint,
            api_version=settings.llm_azure_openai_api_version,
            max_retries=_MAX_RETRIES,
        )

    if provider == "openai":
        kwargs = {"api_key": settings.llm_openai_api_key, "max_retries": _MAX_RETRIES}
        if settings.llm_openai_base_url:
            kwargs["base_url"] = settings.llm_openai_base_url
        return AsyncOpenAI(**kwargs)

    raise ValueError(
        "OpenAI SDK client creation is only supported for providers 'openai' and 'azure-openai'."
    )


def embed_texts_sync(texts: Sequence[str], settings: BackendSettings) -> list[list[float]]:
    provider = settings.embedding_provider
    if provider in {"openai", "azure-openai"}:
        client = create_sync_client(settings, provider=provider)
        embeddings: list[list[float]] = []
        for batch in _batched(list(texts)):
            response = client.embeddings.create(model=_embedding_model(settings), input=batch)
            embeddings.extend(item.embedding for item in response.data)
        return embeddings

    raise ValueError(f"Embedding provider '{provider}' is not supported for sync embeddings.")


async def embed_texts_async(texts: Sequence[str], settings: BackendSettings) -> list[list[float]]:
    provider = settings.embedding_provider
    if provider in {"openai", "azure-openai"}:
        client = create_async_client(settings, provider=provider)
        embeddings: list[list[float]] = []
        for batch in _batched(list(texts)):
            response = await client.embeddings.create(model=_embedding_model(settings), input=batch)
            embeddings.extend(item.embedding for item in response.data)
        return embeddings

    raise ValueError(f"Embedding provider '{provider}' is not supported for async embeddings.")


def complete_json_sync(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: BackendSettings,
    temperature: float = 0.1,
) -> dict:
    # Only the ingest pipeline (wiki-page generation) calls this, so it resolves
    # the ingest provider/model, which fall back to the chat ones when unset.
    provider = settings.ingest_provider
    if provider in {"openai", "azure-openai"}:
        client = create_sync_client(settings, provider=provider)
        response = client.chat.completions.create(
            model=settings.resolved_ingest_model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    raise ValueError(f"Ingest provider '{provider}' is not supported for sync completion.")


async def complete_text_async(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float = 0.1,
    requires_vision: bool = False,
) -> str:
    provider = settings.vision_provider if requires_vision else settings.chat_provider
    if provider in {"openai", "azure-openai"}:
        client = create_async_client(settings, provider=provider)
        response = await client.chat.completions.create(
            model=_chat_model(settings, requires_vision=requires_vision),
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    raise ValueError(f"Chat provider '{provider}' is not supported for async completion.")
