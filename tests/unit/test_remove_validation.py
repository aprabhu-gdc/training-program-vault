"""/remove path validation and preview computation (read-only)."""

from __future__ import annotations

import json

import pytest

from teams_bot.services.admin_preview import (
    RemovePreviewError,
    build_clean_preview,
    build_remove_preview,
    normalize_wiki_path,
)
from tests.conftest import make_core_settings


def test_normalize_appends_md_and_accepts_valid_path():
    assert normalize_wiki_path("wiki/concepts/etc") == "wiki/concepts/etc.md"
    assert normalize_wiki_path("`wiki/sources/foo.md`") == "wiki/sources/foo.md"


@pytest.mark.parametrize(
    "bad",
    ["", "notwiki/foo.md", "wiki/../secrets.md", "wiki/index.md", "wiki/log.md", "wiki/reports/x.md"],
)
def test_invalid_or_protected_paths_are_rejected(bad):
    with pytest.raises(RemovePreviewError):
        normalize_wiki_path(bad)


def _seed_page(settings, rel: str, body: str, frontmatter: str = "title: Foo\ntype: source") -> None:
    path = settings.repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_remove_preview_reports_missing_page(tmp_path):
    settings = make_core_settings(tmp_path)
    with pytest.raises(RemovePreviewError, match="can’t find"):
        build_remove_preview("wiki/sources/missing.md", settings=settings)


def test_remove_preview_flags_inbound_links_and_sources(tmp_path):
    settings = make_core_settings(tmp_path)
    _seed_page(settings, "wiki/sources/foo.md", "body", frontmatter="title: Foo\ntype: source\nsources:\n  - raw/sources/foo.pdf")
    _seed_page(settings, "wiki/concepts/bar.md", "See [[wiki/sources/foo]] for detail.", frontmatter="title: Bar\ntype: concept")
    settings.vector_manifest_path.write_text(json.dumps({"wiki/sources/foo.md": "sha"}), encoding="utf-8")

    preview = build_remove_preview("wiki/sources/foo.md", settings=settings)
    assert preview.relative_path == "wiki/sources/foo.md"
    facts = dict(preview.facts)
    assert facts["Indexed"] == "yes"
    assert facts["Inbound links"] == "1"
    # Both the raw-source resurrection warning and the broken-link warning fire.
    assert any("raw source" in w for w in preview.warnings)
    assert any("link here" in w for w in preview.warnings)


def test_clean_preview_detects_orphaned_index_entries(tmp_path):
    settings = make_core_settings(tmp_path)
    _seed_page(settings, "wiki/sources/live.md", "body")
    # Manifest references a page that no longer exists on disk -> pending deletion.
    settings.vector_manifest_path.write_text(
        json.dumps({"wiki/sources/live.md": "sha", "wiki/sources/gone.md": "sha"}), encoding="utf-8"
    )
    preview = build_clean_preview(settings=settings)
    assert preview.will_delete
    assert preview.delete_paths == ["wiki/sources/gone.md"]
