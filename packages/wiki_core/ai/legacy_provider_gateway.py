"""Compatibility implementation of the model gateway using current provider adapters."""

from __future__ import annotations

from typing import Any, Sequence

from rag_backend.llm import complete_json_sync, complete_text_async, embed_texts_async, embed_texts_sync

from packages.wiki_core.settings import CoreSettings


class LegacyProviderGateway:
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()

    def embed_texts_sync(self, texts: Sequence[str]) -> list[list[float]]:
        return embed_texts_sync(texts, self._settings)

    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]:
        return await embed_texts_async(texts, self._settings)

    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        temperature: float = 0.1,
        requires_vision: bool = False,
    ) -> str:
        return await complete_text_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=self._settings,
            temperature=temperature,
            requires_vision=requires_vision,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict:
        return complete_json_sync(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=self._settings,
            temperature=temperature,
        )
