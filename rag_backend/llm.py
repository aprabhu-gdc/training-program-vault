"""LLM helpers for retrieval and ingestion.

The configuration contract is provider-agnostic. The runtime implementation in
this module supports:

- openai
- azure-openai
- anthropic
- google
"""

from __future__ import annotations

import json
from itertools import islice
from typing import Any, Iterable, Sequence

import httpx
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


def _json_only_system_prompt(system_prompt: str) -> str:
    return (
        system_prompt.rstrip()
        + "\n\nReturn only a valid JSON object. Do not include markdown fences or any prose outside the JSON."
    )


def _normalize_json_text(text: str, *, prefixed_open_brace: bool = False) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if prefixed_open_brace and stripped and not stripped.startswith("{"):
        stripped = "{" + stripped
    return stripped or "{}"


def create_sync_client(settings: BackendSettings, *, provider: str) -> OpenAI | AzureOpenAI:
    if provider == "azure-openai":
        return AzureOpenAI(
            api_key=settings.llm_azure_openai_api_key,
            azure_endpoint=settings.llm_azure_openai_endpoint,
            api_version=settings.llm_azure_openai_api_version,
        )

    if provider == "openai":
        kwargs = {"api_key": settings.llm_openai_api_key}
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
        )

    if provider == "openai":
        kwargs = {"api_key": settings.llm_openai_api_key}
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

    if provider == "google":
        return [_google_embed_sync(text=text, settings=settings) for text in texts]

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

    if provider == "google":
        embeddings: list[list[float]] = []
        for text in texts:
            embeddings.append(await _google_embed_async(text=text, settings=settings))
        return embeddings

    raise ValueError(f"Embedding provider '{provider}' is not supported for async embeddings.")


def complete_json_sync(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: BackendSettings,
    temperature: float = 0.1,
) -> dict:
    provider = settings.chat_provider
    if provider in {"openai", "azure-openai"}:
        client = create_sync_client(settings, provider=provider)
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

    if provider == "anthropic":
        content = _anthropic_complete_sync(
            system_prompt=_json_only_system_prompt(system_prompt),
            user_prompt=user_prompt,
            settings=settings,
            temperature=temperature,
            requires_vision=False,
            assistant_prefill="{",
        )
        return json.loads(_normalize_json_text(content, prefixed_open_brace=True))

    if provider == "google":
        content = _google_complete_sync(
            system_prompt=_json_only_system_prompt(system_prompt),
            user_prompt=user_prompt,
            settings=settings,
            temperature=temperature,
            requires_vision=False,
            response_mime_type="application/json",
        )
        return json.loads(_normalize_json_text(content))

    raise ValueError(f"Chat provider '{provider}' is not supported for sync completion.")


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

    if provider == "anthropic":
        return await _anthropic_complete_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=settings,
            temperature=temperature,
            requires_vision=requires_vision,
        )

    if provider == "google":
        return await _google_complete_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=settings,
            temperature=temperature,
            requires_vision=requires_vision,
        )

    raise ValueError(f"Chat provider '{provider}' is not supported for async completion.")


def _anthropic_headers(settings: BackendSettings) -> dict[str, str]:
    return {
        "x-api-key": settings.llm_anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _anthropic_base_url(settings: BackendSettings) -> str:
    return (settings.llm_anthropic_base_url or "https://api.anthropic.com").rstrip("/")


def _google_base_url(settings: BackendSettings) -> str:
    return (settings.llm_google_base_url or "https://generativelanguage.googleapis.com").rstrip("/")


def _anthropic_complete_sync(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float,
    requires_vision: bool,
    assistant_prefill: str | None = None,
) -> str:
    url = f"{_anthropic_base_url(settings)}/v1/messages"
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": _anthropic_user_content(user_prompt),
        }
    ]
    if assistant_prefill is not None:
        messages.append({"role": "assistant", "content": assistant_prefill})
    payload = {
        "model": _chat_model(settings, requires_vision=requires_vision),
        "max_tokens": 4096,
        "temperature": temperature,
        "system": system_prompt,
        "messages": messages,
    }
    with httpx.Client(timeout=120) as client:
        response = client.post(url, headers=_anthropic_headers(settings), json=payload)
        response.raise_for_status()
        data = response.json()
    return _anthropic_response_text(data)


async def _anthropic_complete_async(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float,
    requires_vision: bool,
) -> str:
    url = f"{_anthropic_base_url(settings)}/v1/messages"
    payload = {
        "model": _chat_model(settings, requires_vision=requires_vision),
        "max_tokens": 4096,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": _anthropic_user_content(user_prompt),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=_anthropic_headers(settings), json=payload)
        response.raise_for_status()
        data = response.json()
    return _anthropic_response_text(data)


def _anthropic_user_content(user_prompt: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if isinstance(user_prompt, str):
        return user_prompt

    content: list[dict[str, Any]] = []
    for item in user_prompt:
        item_type = item.get("type")
        if item_type == "text":
            content.append({"type": "text", "text": str(item.get("text", ""))})
            continue
        if item_type == "image_url":
            image_url = str(item.get("image_url", {}).get("url", ""))
            if image_url.startswith("data:"):
                header, encoded = image_url.split(",", maxsplit=1)
                media_type = header.split(";", maxsplit=1)[0].split(":", maxsplit=1)[1]
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    }
                )
            else:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_url,
                        },
                    }
                )
    return content


def _anthropic_response_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in payload.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _google_complete_sync(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float,
    requires_vision: bool,
    response_mime_type: str | None = None,
) -> str:
    url = _google_generate_url(settings, requires_vision=requires_vision)
    payload = _google_generate_payload(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        response_mime_type=response_mime_type,
    )
    with httpx.Client(timeout=120) as client:
        response = client.post(
            url,
            params={"key": settings.llm_google_api_key},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return _google_response_text(data)


async def _google_complete_async(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    settings: BackendSettings,
    temperature: float,
    requires_vision: bool,
    response_mime_type: str | None = None,
) -> str:
    url = _google_generate_url(settings, requires_vision=requires_vision)
    payload = _google_generate_payload(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        response_mime_type=response_mime_type,
    )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            params={"key": settings.llm_google_api_key},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return _google_response_text(data)


def _google_generate_url(settings: BackendSettings, *, requires_vision: bool) -> str:
    model = _chat_model(settings, requires_vision=requires_vision)
    return f"{_google_base_url(settings)}/v1beta/models/{model}:generateContent"


def _google_embed_url(settings: BackendSettings) -> str:
    model = _embedding_model(settings)
    return f"{_google_base_url(settings)}/v1beta/models/{model}:embedContent"


def _google_generate_payload(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    temperature: float,
    response_mime_type: str | None = None,
) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "temperature": temperature,
    }
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type

    return {
        "systemInstruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": _google_user_parts(user_prompt),
            }
        ],
        "generationConfig": generation_config,
    }


def _google_user_parts(user_prompt: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(user_prompt, str):
        return [{"text": user_prompt}]

    parts: list[dict[str, Any]] = []
    for item in user_prompt:
        item_type = item.get("type")
        if item_type == "text":
            parts.append({"text": str(item.get("text", ""))})
            continue
        if item_type == "image_url":
            image_url = str(item.get("image_url", {}).get("url", ""))
            if image_url.startswith("data:"):
                header, encoded = image_url.split(",", maxsplit=1)
                media_type = header.split(";", maxsplit=1)[0].split(":", maxsplit=1)[1]
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": media_type,
                            "data": encoded,
                        }
                    }
                )
    return parts


def _google_response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            text_parts.append(str(part.get("text", "")).strip())
    return "\n".join(part for part in text_parts if part).strip()


def _google_embed_sync(*, text: str, settings: BackendSettings) -> list[float]:
    url = _google_embed_url(settings)
    payload = {"content": {"parts": [{"text": text}]}}
    with httpx.Client(timeout=120) as client:
        response = client.post(url, params={"key": settings.llm_google_api_key}, json=payload)
        response.raise_for_status()
        data = response.json()
    return _google_embedding_from_payload(data)


async def _google_embed_async(*, text: str, settings: BackendSettings) -> list[float]:
    url = _google_embed_url(settings)
    payload = {"content": {"parts": [{"text": text}]}}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, params={"key": settings.llm_google_api_key}, json=payload)
        response.raise_for_status()
        data = response.json()
    return _google_embedding_from_payload(data)


def _google_embedding_from_payload(payload: dict[str, Any]) -> list[float]:
    embedding = payload.get("embedding") or {}
    values = embedding.get("values") or []
    return [float(value) for value in values]
