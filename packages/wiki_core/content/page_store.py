"""Interfaces for reading and writing maintained wiki content."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .markdown import WikiPage


class PageStore(Protocol):
    def iter_wiki_pages(self) -> list[Path]: ...

    def load_wiki_page(self, path: Path) -> WikiPage: ...

    def write_page(self, relative_path: str, frontmatter: dict[str, Any], body: str) -> None: ...

    def read_index_summary(self, max_chars: int) -> str: ...

    def upsert_index_entry(self, entry: str) -> bool: ...

    def append_overview_note(self, note: str) -> bool: ...

    def append_log_entry(self, title: str, bullets: list[str]) -> bool: ...
