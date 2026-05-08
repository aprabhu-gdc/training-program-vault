"""Vector store interface for retrieval backends."""

from __future__ import annotations

from typing import Any, Iterable, Protocol


class VectorStore(Protocol):
    def is_ready(self) -> bool: ...

    def rebuild(self, rows: list[dict[str, Any]]) -> None: ...

    def upsert(self, rows: list[dict[str, Any]]) -> None: ...

    def delete_by_paths(self, relative_paths: Iterable[str]) -> None: ...

    def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
