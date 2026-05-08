"""Domain models used by retrieval services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievedChunk:
    document: str
    metadata: dict[str, Any]
