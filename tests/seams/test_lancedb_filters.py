"""LanceDbVectorStore filtered search against a real (tmp-dir) LanceDB table."""

from __future__ import annotations

from packages.wiki_core.retrieval.lancedb_adapter import LanceDbVectorStore
from tests.conftest import make_core_settings


def _row(row_id: str, page_type: str, path: str, vector: list[float]) -> dict:
    return {
        "id": row_id,
        "path": path,
        "title": row_id.replace("-", " ").title(),
        "type": page_type,
        "section": "Overview",
        "chunk_index": 0,
        "sha256": "0" * 64,
        "sources": "[]",
        "text": f"content of {row_id}",
        "vector": vector,
    }


def test_search_with_type_filter_returns_only_matching_rows(tmp_path):
    store = LanceDbVectorStore(make_core_settings(tmp_path))
    store.rebuild(
        [
            _row("source-a", "source", "wiki/sources/a.md", [1.0, 0.0, 0.0]),
            _row("source-b", "source", "wiki/sources/b.md", [0.9, 0.1, 0.0]),
            _row("concept-c", "concept", "wiki/concepts/c.md", [0.0, 1.0, 0.0]),
        ]
    )

    unfiltered = store.search([1.0, 0.0, 0.0], top_k=3)
    assert len(unfiltered) == 3
    assert unfiltered[0]["id"] == "source-a"

    concepts_only = store.search([1.0, 0.0, 0.0], top_k=3, filters={"type": "concept"})
    assert [row["id"] for row in concepts_only] == ["concept-c"]
    assert concepts_only[0]["_distance"] is not None


def test_filter_values_with_quotes_are_escaped(tmp_path):
    store = LanceDbVectorStore(make_core_settings(tmp_path))
    store.rebuild([_row("concept-c", "concept", "wiki/concepts/c.md", [0.0, 1.0, 0.0])])

    # Must not raise or match anything; the quote is escaped, not an injection.
    assert store.search([0.0, 1.0, 0.0], top_k=3, filters={"type": "conc'ept"}) == []
