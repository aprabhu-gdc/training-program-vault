"""Short dashboard labels for wiki concepts."""

from __future__ import annotations

import pytest

from teams_bot.services.concept_labels import CONCEPT_LABEL_OVERRIDES, concept_label


@pytest.mark.parametrize("slug,expected", list(CONCEPT_LABEL_OVERRIDES.items()))
def test_all_overrides_resolve_by_slug(slug, expected):
    # Title deliberately differs from the override to prove slug wins.
    assert concept_label("Some Long Title", f"wiki/concepts/{slug}.md") == expected


def test_leading_all_caps_token_becomes_the_label():
    assert concept_label("RAMP Credit Card Coding (Accounts & Rules)", "wiki/concepts/new.md") == "RAMP"


def test_multiword_title_becomes_acronym():
    assert concept_label("Estimate to Complete", "wiki/concepts/new.md") == "ETC"
    # "Graydaze" is a stopword, so it doesn't pollute the acronym.
    assert concept_label("Graydaze Safety Program Overview", "wiki/concepts/new.md") == "SPO"


def test_short_title_passes_through_first_words():
    assert concept_label("Billing", "wiki/concepts/new.md") == "Billing"
    assert concept_label("Earned Revenue", "wiki/concepts/new.md") == "Earned Revenue"


def test_no_path_still_uses_heuristic():
    assert concept_label("Estimate to Complete") == "ETC"


def test_unknown_and_empty_pass_through():
    assert concept_label("Unknown") == "Unknown"
    assert concept_label("Unknown", "wiki/concepts/whatever.md") == "Unknown"
    assert concept_label("") == "Unknown"


def test_label_length_is_capped():
    assert len(concept_label("Supercalifragilistic Expialidocious Terminology Extravaganza")) <= 20
