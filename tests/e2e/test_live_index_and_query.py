"""Phase 09 LIVE smoke: build a real index from wiki/ and answer a real query.

Exercises the stripped-down single-provider stack end to end against real
Azure OpenAI (embeddings + chat) and a throwaway local LanceDB index. It does
NOT touch SharePoint/Graph or Service Bus.

Run explicitly:  pytest -m live -q
Skipped automatically when LLM credentials are absent (see `live_settings`).
"""

from __future__ import annotations

import dataclasses

import pytest

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import QueryRequest
from packages.wiki_core.content.markdown import iter_wiki_markdown_files
from packages.wiki_core.retrieval.index_service import VaultIndexer
from packages.wiki_core.retrieval.query_service import QueryService

pytestmark = [pytest.mark.live, pytest.mark.slow]


@pytest.fixture
def isolated_live_settings(live_settings, tmp_path):
    """Real LLM/wiki config, but vector DB + manifest redirected to tmp so the
    build neither reads nor clobbers any real local index."""
    if not iter_wiki_markdown_files(live_settings.wiki_root):
        pytest.skip(f"No wiki markdown files under {live_settings.wiki_root}")
    return dataclasses.replace(
        live_settings,
        vector_db_path=tmp_path / "lancedb",
        vector_manifest_path=tmp_path / "index-manifest.json",
        vector_table_name="e2e-smoke-wiki",
    )


async def test_build_index_then_query_returns_grounded_answer(isolated_live_settings):
    settings = isolated_live_settings

    # 1. Build a fresh index from the real wiki/ corpus (real embeddings).
    report = VaultIndexer(settings).build()
    assert report.mode == "build"
    assert report.chunk_count > 0, "index build produced no chunks"
    assert report.indexed_files, "no wiki files were indexed"

    # 2. Query it through the same settings (real chat completion).
    service = QueryService(settings)
    assert service._vector_store.is_ready(), "vector store not ready after build"

    request = QueryRequest(
        request_id="e2e-smoke-1",
        query="How does a project manager start up a new job?",
        identity=CallerIdentity(
            user_id=None, user_name=None, tenant_id=None, client_app="e2e-smoke",
            channel_id=None, conversation_id=None, locale=None,
        ),
    )
    response = await service.query(request)

    # 3. Assert a grounded, cited answer came back.
    assert response.answer_text.strip(), "empty answer text"
    assert response.citations, "no citations returned"
    assert "[Source:" in response.answer_text, (
        "answer is missing the [Source: Title] grounding markers required by the "
        f"system prompt. Got: {response.answer_text[:300]!r}"
    )
    assert response.retrieval_diagnostics.get("chunk_ids"), "no retrieval diagnostics"
