"""OpenAI and Azure OpenAI helpers for retrieval and ingestion."""

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
    if settings.uses_azure_openai:
        if requires_vision and settings.azure_openai_vision_deployment:
            return settings.azure_openai_vision_deployment
        return settings.azure_openai_chat_deployment
    if requires_vision and settings.openai_vision_model:
        return settings.openai_vision_model
    return settings.openai_chat_model


def _embedding_model(settings: BackendSettings) -> str:
    if settings.uses_azure_openai:
        return settings.azure_openai_embedding_deployment
    return settings.openai_embedding_model


def create_sync_client(settings: BackendSettings) -> OpenAI | AzureOpenAI:
    if settings.uses_azure_openai:
        return AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )

    kwargs = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return OpenAI(**kwargs)


def create_async_client(settings: BackendSettings) -> AsyncOpenAI | AsyncAzureOpenAI:
    if settings.uses_azure_openai:
        return AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )

    kwargs = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return AsyncOpenAI(**kwargs)


def embed_texts_sync(texts: Sequence[str], settings: BackendSettings) -> list[list[float]]:
    client = create_sync_client(settings)
    embeddings: list[list[float]] = []
    for batch in _batched(list(texts)):
        response = client.embeddings.create(model=_embedding_model(settings), input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


async def embed_texts_async(texts: Sequence[str], settings: BackendSettings) -> list[list[float]]:
    client = create_async_client(settings)
    embeddings: list[list[float]] = []
    for batch in _batched(list(texts)):
        response = await client.embeddings.create(model=_embedding_model(settings), input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def complete_json_sync(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: BackendSettings,
    temperature: float = 0.1,
) -> dict:
    client = create_sync_client(settings)
    response = client.chat.completions.create(
        model=_chat_model(settings),
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


async def complete_text_async(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float = 0.1,
    requires_vision: bool = False,
) -> str:
    client = create_async_client(settings)
    user_content: Any = user_prompt
    response = await client.chat.completions.create(
        model=_chat_model(settings, requires_vision=requires_vision),
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return (response.choices[0].message.content or "").strip()
