"""Concept derivation: mapping rank-ordered citations to analytics concept titles."""

from __future__ import annotations

from packages.contracts.query import Citation
from teams_bot.services.analytics import MAX_CONCEPTS_PER_QUERY, UNKNOWN_CONCEPT, derive_concepts


def _concept(title: str, path: str | None = None) -> Citation:
    return Citation(
        title=title,
        path=path or f"wiki/concepts/{title.lower().replace(' ', '-')}.md",
        page_type="concept",
    )


def _source(title: str) -> Citation:
    return Citation(title=title, path=f"wiki/sources/{title}.md", page_type="source")


def test_concept_citations_map_to_titles_in_rank_order():
    citations = (_source("s1"), _concept("Estimate to Complete"), _concept("Mission Support"))
    assert derive_concepts(citations) == ("Estimate to Complete", "Mission Support")


def test_source_entity_and_index_chunks_are_ignored():
    citations = (
        _source("etc-training"),
        Citation(title="E", path="wiki/entities/e.md", page_type="entity"),
        Citation(title="Index", path="wiki/index.md", page_type="index"),
    )
    assert derive_concepts(citations) == (UNKNOWN_CONCEPT,)


def test_no_citations_yields_unknown():
    assert derive_concepts(()) == (UNKNOWN_CONCEPT,)
    assert derive_concepts(None) == (UNKNOWN_CONCEPT,)


def test_duplicate_pages_dedupe_preserving_first_rank():
    citations = (
        _concept("Estimate to Complete"),
        _concept("Estimate to Complete"),
        _concept("Mission Support"),
    )
    assert derive_concepts(citations) == ("Estimate to Complete", "Mission Support")


def test_concept_count_is_capped():
    citations = tuple(_concept(f"Concept {i}") for i in range(6))
    assert len(derive_concepts(citations)) == MAX_CONCEPTS_PER_QUERY


def test_path_prefix_fallback_for_index_rows_without_page_type():
    # Rows embedded before page_type existed come back with page_type=None.
    citation = Citation(title="Ramp Credit Card Coding", path="wiki/concepts/ramp.md")
    assert derive_concepts((citation,)) == ("Ramp Credit Card Coding",)


def test_untitled_concept_gets_placeholder_title():
    citation = Citation(title="  ", path="wiki/concepts/x.md", page_type="concept")
    assert derive_concepts((citation,)) == ("Untitled",)
