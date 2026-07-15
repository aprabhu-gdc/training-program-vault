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


# --- source->concept mapping (the inverse index) ---


SOURCE_MAP = {
    "wiki/sources/etc-training.md": ("Estimate to Complete",),
    "wiki/sources/pm-101-crd.md": ("Estimate to Complete", "Graydaze Project Manager Role"),
    "wiki/sources/ramp-guide.md": ("Ramp Credit Card Coding",),
}


def test_source_citation_maps_to_citing_concept():
    citations = (_source("etc-training"),)
    assert derive_concepts(citations, SOURCE_MAP) == ("Estimate to Complete",)


def test_source_cited_by_two_concepts_yields_both():
    citations = (_source("pm-101-crd"),)
    assert derive_concepts(citations, SOURCE_MAP) == (
        "Estimate to Complete",
        "Graydaze Project Manager Role",
    )


def test_mixed_citations_preserve_retrieval_rank_and_dedupe_by_title():
    citations = (
        _source("etc-training"),          # -> Estimate to Complete
        _concept("Mission Support"),      # direct concept
        _source("pm-101-crd"),            # -> Estimate to Complete (dup) + PM Role
    )
    assert derive_concepts(citations, SOURCE_MAP) == (
        "Estimate to Complete",
        "Mission Support",
        "Graydaze Project Manager Role",
    )


def test_cap_applies_across_mapped_titles():
    citations = (
        _source("pm-101-crd"),   # two titles
        _source("ramp-guide"),   # third title reaches the cap
        _concept("Mission Support"),  # beyond cap, dropped
    )
    result = derive_concepts(citations, SOURCE_MAP)
    assert len(result) == 3
    assert "Mission Support" not in result


def test_unmapped_source_still_yields_unknown():
    citations = (_source("uncited-source"),)
    assert derive_concepts(citations, SOURCE_MAP) == ("Unknown",)


def test_no_map_keeps_legacy_behavior():
    citations = (_source("etc-training"),)
    assert derive_concepts(citations) == ("Unknown",)
    assert derive_concepts(citations, None) == ("Unknown",)
