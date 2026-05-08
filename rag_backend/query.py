"""Compatibility wrapper for the extracted wiki query service."""

from __future__ import annotations

from packages.wiki_core.retrieval.query_service import QueryService, query_vault_structured


async def query_vault(query: str, request_id: str, **kwargs) -> str:
    response = await query_vault_structured(query=query, request_id=request_id, **kwargs)
    return response.answer_text


__all__ = ["QueryService", "query_vault", "query_vault_structured"]
