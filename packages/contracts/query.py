"""Shared query request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .identity import CallerIdentity


@dataclass(frozen=True)
class QueryAttachment:
    name: str
    content_type: str
    text_content: str | None = None
    image_data_url: str | None = None
    blob_ref: str | None = None


@dataclass(frozen=True)
class QueryRequest:
    request_id: str
    query: str
    identity: CallerIdentity
    attachments: tuple[QueryAttachment, ...] = ()
    client_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Citation:
    title: str
    path: str
    section: str | None = None
    sources: tuple[str, ...] = ()
    # Wiki page type from frontmatter (concept | source | entity | index).
    page_type: str | None = None


@dataclass(frozen=True)
class QueryResponse:
    answer_text: str
    citations: tuple[Citation, ...] = ()
    warnings: tuple[str, ...] = ()
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)
