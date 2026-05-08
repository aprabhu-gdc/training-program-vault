"""Protocol for model operations used by wiki core services."""

from __future__ import annotations

from typing import Any, Protocol, Sequence


class ModelGateway(Protocol):
    def embed_texts_sync(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        temperature: float = 0.1,
        requires_vision: bool = False,
    ) -> str: ...

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict: ...
