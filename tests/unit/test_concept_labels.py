"""Readable dashboard labels for wiki concepts."""

from __future__ import annotations

import pytest

from teams_bot.services.concept_labels import (
    CONCEPT_LABEL_OVERRIDES,
    MAX_LABEL_CHARS,
    concept_label,
)


@pytest.mark.parametrize("slug,expected", list(CONCEPT_LABEL_OVERRIDES.items()))
def test_all_overrides_resolve_by_slug(slug, expected):
    # Title deliberately differs from the override to prove slug wins.
    assert concept_label("Some Long Title", f"wiki/concepts/{slug}.md") == expected


def test_curated_labels_are_within_the_cap():
    for label in CONCEPT_LABEL_OVERRIDES.values():
        assert len(label) <= MAX_LABEL_CHARS, label


def test_heuristic_keeps_readable_title_not_an_acronym():
    # A multi-word title with no override passes through readably, never an
    # invented initialism like "ETC".
    assert concept_label("Estimate to Complete", "wiki/concepts/new.md") == "Estimate to Complete"
    assert (
        concept_label("Joint Filler Replacement", "wiki/concepts/new.md")
        == "Joint Filler Replacement"
    )


def test_heuristic_preserves_a_genuine_acronym_title():
    # A title that already reads as an acronym is passed through, not mangled.
    assert (
        concept_label("OSHA Flammable Liquids", "wiki/concepts/new.md")
        == "OSHA Flammable Liquids"
    )


def test_heuristic_drops_leading_org_or_article_prefix():
    assert concept_label("Graydaze Safety Program", "wiki/concepts/new.md") == "Safety Program"
    assert concept_label("The Road to Success", "wiki/concepts/new.md") == "Road to Success"


def test_short_title_passes_through():
    assert concept_label("Billing", "wiki/concepts/new.md") == "Billing"
    assert concept_label("Earned Revenue", "wiki/concepts/new.md") == "Earned Revenue"


def test_no_path_still_uses_heuristic():
    assert concept_label("Estimate to Complete") == "Estimate to Complete"


def test_unknown_and_empty_pass_through():
    assert concept_label("Unknown") == "Unknown"
    assert concept_label("Unknown", "wiki/concepts/whatever.md") == "Unknown"
    assert concept_label("") == "Unknown"


def test_label_length_is_capped():
    label = concept_label("Supercalifragilistic Expialidocious Terminology Extravaganza")
    assert len(label) <= MAX_LABEL_CHARS
    assert label.endswith("…")
