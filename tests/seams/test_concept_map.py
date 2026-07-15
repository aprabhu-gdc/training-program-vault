"""ConceptMapResolver: inverse map built from real wiki frontmatter on disk."""

from __future__ import annotations

import logging

from packages.contracts.query import Citation
from teams_bot.services.analytics import ConceptMapResolver, derive_concepts
from tests.conftest import make_core_settings


CONCEPT_PAGE = """---
title: Estimate to Complete
type: concept
status: active
sources:
  - wiki/sources/etc-training.md
  - wiki/sources/pm-101-crd.md
  - raw/sources/etc-training.docx
---

## Current Synthesis

ETC content.
"""

SECOND_CONCEPT_PAGE = """---
title: Graydaze Project Manager Role
type: concept
sources:
  - wiki/sources/pm-101-crd.md
---

## Current Synthesis

Role content.
"""

SOURCE_PAGE = """---
title: ETC Training
type: source
sources:
  - raw/sources/etc-training.docx
---

## Extract

Raw extract.
"""


def _write_wiki(settings) -> None:
    concepts = settings.wiki_root / "concepts"
    sources = settings.wiki_root / "sources"
    concepts.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    (concepts / "estimate-to-complete.md").write_text(CONCEPT_PAGE, encoding="utf-8")
    (concepts / "graydaze-project-manager-role.md").write_text(SECOND_CONCEPT_PAGE, encoding="utf-8")
    (sources / "etc-training.md").write_text(SOURCE_PAGE, encoding="utf-8")


def test_builds_inverse_map_from_concept_frontmatter(tmp_path):
    settings = make_core_settings(tmp_path)
    _write_wiki(settings)

    mapping = ConceptMapResolver(settings).mapping()

    assert mapping["wiki/sources/etc-training.md"] == ("Estimate to Complete",)
    assert mapping["wiki/sources/pm-101-crd.md"] == (
        "Estimate to Complete",
        "Graydaze Project Manager Role",
    )
    # raw/ entries and source pages themselves are not keys.
    assert not any(key.startswith("raw/") for key in mapping)


def test_end_to_end_source_citation_classifies_concept(tmp_path):
    settings = make_core_settings(tmp_path)
    _write_wiki(settings)
    mapping = ConceptMapResolver(settings).mapping()

    citation = Citation(title="ETC Training", path="wiki/sources/etc-training.md", page_type="source")
    assert derive_concepts((citation,), mapping) == ("Estimate to Complete",)


def test_broken_settings_fail_soft_with_single_warning(caplog):
    # A settings object without wiki_root makes the build raise; the resolver
    # must swallow it, return the empty map, and warn only once.
    resolver = ConceptMapResolver(object(), ttl_seconds=0.0)

    with caplog.at_level(logging.WARNING, logger="teams_bot.services.analytics"):
        assert resolver.mapping() == {}
        assert resolver.mapping() == {}

    warnings = [r for r in caplog.records if "source->concept map" in r.message]
    assert len(warnings) == 1


def test_malformed_page_is_skipped(tmp_path):
    settings = make_core_settings(tmp_path)
    _write_wiki(settings)
    # A file that is not valid UTF-8 makes load_wiki_page raise for that page.
    (settings.wiki_root / "concepts" / "broken.md").write_bytes(b"\xff\xfe\x00broken")

    mapping = ConceptMapResolver(settings).mapping()
    assert mapping["wiki/sources/etc-training.md"] == ("Estimate to Complete",)


def test_ttl_refresh_picks_up_new_concepts(tmp_path):
    settings = make_core_settings(tmp_path)
    _write_wiki(settings)
    resolver = ConceptMapResolver(settings, ttl_seconds=0.0)
    assert "wiki/sources/new-source.md" not in resolver.mapping()

    (settings.wiki_root / "concepts" / "new-concept.md").write_text(
        "---\ntitle: New Concept\ntype: concept\nsources:\n  - wiki/sources/new-source.md\n---\n\nBody.\n",
        encoding="utf-8",
    )
    assert resolver.mapping()["wiki/sources/new-source.md"] == ("New Concept",)