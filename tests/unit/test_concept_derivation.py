"""derive_concept: classify a query as the single most relevant wiki concept."""

from __future__ import annotations

from packages.contracts.query import Citation
from teams_bot.services.analytics import (
    UNKNOWN_CONCEPT,
    ConceptMatch,
    derive_concept,
)


def _concept(title: str, path: str | None = None) -> Citation:
    return Citation(
        title=title,
        path=path or f"wiki/concepts/{title.lower().replace(' ', '-')}.md",
        page_type="concept",
    )


def _source(title: str) -> Citation:
    return Citation(title=title, path=f"wiki/sources/{title}.md", page_type="source")


CANDIDATES = (
    {"title": "Estimate to Complete", "path": "wiki/concepts/estimate-to-complete.md", "distance": 1.06},
    {"title": "Mission Support", "path": "wiki/concepts/mission-support.md", "distance": 1.30},
)

SOURCE_MAP = {
    "wiki/sources/etc-training.md": (ConceptMatch("Estimate to Complete", "wiki/concepts/estimate-to-complete.md"),),
    "wiki/sources/pm-101-crd.md": (
        ConceptMatch("Estimate to Complete", "wiki/concepts/estimate-to-complete.md"),
        ConceptMatch("Graydaze Project Manager Role", "wiki/concepts/graydaze-project-manager-role.md"),
    ),
}


# --- primary: nearest concept candidate under the absolute distance ceiling ---


def test_nearest_candidate_under_ceiling_wins():
    match = derive_concept((_source("x"),), {}, concept_candidates=CANDIDATES)
    # 1.06 <= 1.5 default ceiling; the nearest candidate is chosen.
    assert match == ConceptMatch("Estimate to Complete", "wiki/concepts/estimate-to-complete.md")


def test_only_the_nearest_candidate_decides():
    # Candidates are distance-ordered; the first (nearest) decides the outcome
    # even though a later candidate is also under the ceiling.
    near = (
        {"title": "ETC", "path": "wiki/concepts/etc.md", "distance": 1.00},
        {"title": "Mission Support", "path": "wiki/concepts/mission-support.md", "distance": 1.20},
    )
    assert derive_concept((_source("x"),), {}, concept_candidates=near).title == "ETC"


def test_nearest_candidate_beyond_ceiling_is_unknown():
    # Off-topic: the nearest concept is far, so no concept applies.
    far = ({"title": "Estimate to Complete", "path": "wiki/concepts/x.md", "distance": 1.76},)
    assert derive_concept((_source("x"),), {}, concept_candidates=far) == ConceptMatch(UNKNOWN_CONCEPT)


def test_custom_max_distance_is_honored():
    far = ({"title": "ETC", "path": "wiki/concepts/etc.md", "distance": 1.4},)
    assert derive_concept((), concept_candidates=far, max_distance=1.0) == ConceptMatch(UNKNOWN_CONCEPT)
    assert derive_concept((), concept_candidates=far, max_distance=1.5).title == "ETC"


def test_candidate_gate_ignores_citations_and_map():
    # When candidates are present, the ceiling alone decides — a mapped source
    # citation must not resurrect an off-topic query as a concept.
    far = ({"title": "ETC", "path": "wiki/concepts/etc.md", "distance": 1.9},)
    match = derive_concept((_source("etc-training"),), SOURCE_MAP, concept_candidates=far)
    assert match == ConceptMatch(UNKNOWN_CONCEPT)


def test_candidate_skips_non_numeric_distance_then_uses_next():
    mixed = (
        {"title": "ETC", "path": "wiki/concepts/etc.md", "distance": None},
        {"title": "Mission Support", "path": "wiki/concepts/mission-support.md", "distance": 1.1},
    )
    assert derive_concept((), concept_candidates=mixed).title == "Mission Support"


# --- fallback (no candidates): first concept-typed citation ---


def test_concept_citation_used_when_no_candidates():
    citations = (_source("s1"), _concept("Estimate to Complete"), _concept("Mission Support"))
    match = derive_concept(citations)
    assert match.title == "Estimate to Complete"
    assert match.path == "wiki/concepts/estimate-to-complete.md"


def test_empty_candidates_falls_back_to_citation():
    match = derive_concept((_concept("Estimate to Complete"),), {}, concept_candidates=[])
    assert match.title == "Estimate to Complete"


def test_path_prefix_fallback_for_rows_without_page_type():
    citation = Citation(title="Ramp Credit Card Coding", path="wiki/concepts/ramp.md")
    assert derive_concept((citation,)).title == "Ramp Credit Card Coding"


def test_untitled_concept_gets_placeholder():
    assert derive_concept((Citation(title="  ", path="wiki/concepts/x.md", page_type="concept"),)).title == "Untitled"


# --- pass 3: source-to-concept map ---


def test_source_citation_maps_to_first_citing_concept():
    match = derive_concept((_source("pm-101-crd"),), SOURCE_MAP)
    assert match == ConceptMatch("Estimate to Complete", "wiki/concepts/estimate-to-complete.md")


def test_source_and_entity_without_map_is_unknown():
    citations = (
        _source("etc-training"),
        Citation(title="E", path="wiki/entities/e.md", page_type="entity"),
    )
    assert derive_concept(citations) == ConceptMatch(UNKNOWN_CONCEPT)


def test_unmapped_source_is_unknown():
    assert derive_concept((_source("uncited"),), SOURCE_MAP) == ConceptMatch(UNKNOWN_CONCEPT)


# --- empty / no match ---


def test_no_citations_is_unknown():
    assert derive_concept(()) == ConceptMatch(UNKNOWN_CONCEPT)
    assert derive_concept(None) == ConceptMatch(UNKNOWN_CONCEPT)


def test_candidate_takes_priority_over_citation():
    # The concept-candidate signal wins over a different concept citation.
    citations = (_concept("Mission Support"),)
    match = derive_concept(citations, {}, concept_candidates=CANDIDATES)
    assert match.title == "Estimate to Complete"
