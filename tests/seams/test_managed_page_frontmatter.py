"""Phase 09: write_managed_page strips embedded frontmatter (no duplicate ---)."""

from __future__ import annotations

from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import split_frontmatter


def _frontmatter_delimiter_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() == "---")


def test_embedded_frontmatter_is_stripped_to_single_block(core_settings):
    store = FilePageStore(core_settings)
    body_with_embedded_fm = (
        "---\n"
        "title: LLM Embedded Title\n"
        "type: source\n"
        "status: draft\n"
        "---\n\n"
        "# Real Content\n\nThis is the actual body text."
    )
    result = store.write_managed_page(
        "wiki/sources/test.md",
        {"title": "Canonical Title", "type": "source", "body": body_with_embedded_fm},
        "raw/sources/test.docx",
    )
    assert result == "wiki/sources/test.md"

    written = (core_settings.repo_root / "wiki/sources/test.md").read_text(encoding="utf-8")
    # Exactly one frontmatter block => exactly two `---` delimiter lines.
    assert _frontmatter_delimiter_count(written) == 2
    # The canonical title wins; the embedded one is discarded.
    fm, page_body = split_frontmatter(written)
    assert fm["title"] == "Canonical Title"
    assert "LLM Embedded Title" not in written
    assert page_body.lstrip().startswith("# Real Content")


def test_plain_body_produces_single_block(core_settings):
    store = FilePageStore(core_settings)
    result = store.write_managed_page(
        "wiki/concepts/plain.md",
        {"title": "Plain", "type": "concept", "body": "# Heading\n\nBody only, no frontmatter."},
        "raw/sources/plain.docx",
    )
    written = (core_settings.repo_root / result).read_text(encoding="utf-8")
    assert _frontmatter_delimiter_count(written) == 2
    fm, _ = split_frontmatter(written)
    assert fm["title"] == "Plain"


def test_sources_are_merged_on_rewrite(core_settings):
    store = FilePageStore(core_settings)
    store.write_managed_page(
        "wiki/sources/m.md",
        {"title": "M", "type": "source", "body": "Body v1", "sources": ["raw/sources/one.docx"]},
        "raw/sources/one.docx",
    )
    store.write_managed_page(
        "wiki/sources/m.md",
        {"title": "M", "type": "source", "body": "Body v2", "sources": ["raw/sources/two.docx"]},
        "raw/sources/two.docx",
    )
    fm, _ = split_frontmatter((core_settings.repo_root / "wiki/sources/m.md").read_text(encoding="utf-8"))
    assert fm["sources"] == ["raw/sources/one.docx", "raw/sources/two.docx"]
    assert fm["source_count"] >= 2


def test_rejects_non_wiki_path_and_empty_body(core_settings):
    store = FilePageStore(core_settings)
    assert store.write_managed_page("notwiki/x.md", {"body": "x"}, "raw/sources/x.docx") is None
    assert store.write_managed_page("wiki/x.txt", {"body": "x"}, "raw/sources/x.docx") is None
    # Body that is only frontmatter (nothing left after stripping) => None.
    assert store.write_managed_page(
        "wiki/sources/empty.md", {"body": "---\ntitle: x\n---\n"}, "raw/sources/x.docx"
    ) is None
