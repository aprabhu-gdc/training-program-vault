"""Phase 09: QueryService retrieval->prompt->answer plumbing (LLM + vector mocked)."""

from __future__ import annotations

import pytest

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import QueryRequest
from packages.wiki_core.retrieval.query_service import QueryService


ROW = {
    "id": "chunk-1",
    "title": "My Title",
    "section": "Overview",
    "path": "wiki/sources/x.md",
    "sources": '["raw/sources/x.docx"]',
    "type": "source",
    "text": "Relevant content about the topic.",
    "_distance": 0.12,
}


class FakeVectorStore:
    def __init__(self, rows, ready=True):
        self._rows = rows
        self._ready = ready

    def is_ready(self):
        return self._ready

    def search(self, embedding, *, top_k, filters=None):
        self.last_top_k = top_k
        return self._rows


class FakeGateway:
    def __init__(self, answer="The answer is grounded. [Source: My Title]"):
        self.answer = answer
        self.captured = {}

    async def embed_texts_async(self, texts):
        self.captured["embed_texts"] = list(texts)
        return [[0.1, 0.2, 0.3]]

    async def complete_text(self, *, system_prompt, user_prompt, temperature=0.1, requires_vision=False):
        self.captured["system_prompt"] = system_prompt
        self.captured["user_prompt"] = user_prompt
        self.captured["requires_vision"] = requires_vision
        return self.answer


def _request(query="How do I start a job?"):
    return QueryRequest(
        request_id="req-1",
        query=query,
        identity=CallerIdentity(
            user_id=None, user_name=None, tenant_id=None, client_app="test",
            channel_id=None, conversation_id=None, locale=None,
        ),
    )


def _service(core_settings, rows, ready=True, answer=None):
    service = QueryService(core_settings)
    service._vector_store = FakeVectorStore(rows, ready=ready)
    service._model_gateway = FakeGateway(answer) if answer else FakeGateway()
    return service


async def test_query_returns_answer_and_citations(core_settings):
    service = _service(core_settings, [ROW])
    resp = await service.query(_request())

    assert resp.answer_text == "The answer is grounded. [Source: My Title]"
    assert len(resp.citations) == 1
    citation = resp.citations[0]
    assert citation.title == "My Title"
    assert citation.path == "wiki/sources/x.md"
    assert citation.sources == ("raw/sources/x.docx",)
    assert resp.retrieval_diagnostics["top_k"] == core_settings.rag_top_k
    assert resp.retrieval_diagnostics["chunk_ids"] == ["chunk-1"]


async def test_query_passes_retrieved_context_into_prompt(core_settings):
    service = _service(core_settings, [ROW])
    await service.query(_request())
    # The retrieved chunk text must reach the model's user prompt.
    assert "Relevant content about the topic." in service._model_gateway.captured["user_prompt"]
    assert service._model_gateway.captured["requires_vision"] is False


async def test_query_no_results_returns_fallback(core_settings):
    service = _service(core_settings, [])
    resp = await service.query(_request())
    assert "find anything relevant" in resp.answer_text
    assert resp.citations == ()


async def test_query_raises_when_index_not_ready(core_settings):
    service = _service(core_settings, [], ready=False)
    with pytest.raises(RuntimeError, match="index is not ready"):
        await service.query(_request())
