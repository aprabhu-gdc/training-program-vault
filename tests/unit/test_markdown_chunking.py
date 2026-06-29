"""Phase 09: markdown chunking, the 6000-char cap, and frontmatter helpers."""

from __future__ import annotations

from pathlib import Path

from packages.wiki_core.content.markdown import (
    MAX_CHUNK_CHARS,
    build_chunks_for_page,
    compose_markdown,
    dump_frontmatter,
    load_wiki_page,
    split_by_h2_sections,
    split_frontmatter,
)


def _write_page(wiki: Path, name: str, text: str) -> Path:
    path = wiki / name
    path.write_text(text, encoding="utf-8")
    return path


def test_split_frontmatter_roundtrips_with_compose():
    frontmatter = {"title": "Hello", "type": "concept", "sources": ["raw/sources/x.md"]}
    body = "# Heading\n\nSome body text.\n"
    composed = compose_markdown(frontmatter, body)

    parsed_fm, parsed_body = split_frontmatter(composed)
    assert parsed_fm == frontmatter
    assert parsed_body.strip() == body.strip()


def test_split_frontmatter_no_block_returns_empty_and_original():
    text = "No frontmatter here.\n\nJust body."
    fm, body = split_frontmatter(text)
    assert fm == {}
    assert body == text


def test_dump_frontmatter_preserves_key_order():
    dumped = dump_frontmatter({"title": "A", "type": "source", "status": "active"})
    # sort_keys=False, so insertion order is preserved.
    assert dumped.splitlines()[0].startswith("title:")
    assert dumped.splitlines()[1].startswith("type:")


def test_split_by_h2_sections_splits_on_h2_headings():
    body = "Intro line.\n\n## First\n\nalpha\n\n## Second\n\nbeta"
    sections = split_by_h2_sections(body)
    headings = [heading for heading, _ in sections]
    assert "First" in headings
    assert "Second" in headings


def test_oversized_section_is_split_under_cap(wiki_dir):
    repo_root, wiki = wiki_dir
    # One H2 section whose body is ~3x the cap, built from paragraphs so the
    # splitter has natural break points.
    paragraph = ("word " * 200).strip()  # ~1000 chars
    big_body = "\n\n".join([paragraph] * 20)  # ~20k chars, well over 6000
    text = (
        "---\ntitle: Big Page\ntype: source\nsources: []\n---\n\n"
        "## Big Section\n\n" + big_body + "\n"
    )
    page_path = _write_page(wiki, "big.md", text)

    page = load_wiki_page(page_path, repo_root)
    chunks = build_chunks_for_page(page)

    assert len(chunks) > 1, "oversized section should produce multiple chunks"
    # The metadata prefix adds a few lines; assert the *chunk body* portion that
    # the splitter controls never exceeds the cap. The full `text` includes a
    # short fixed prefix, so allow a small prefix allowance.
    prefix_allowance = 200
    for chunk in chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS + prefix_allowance
        # All parts of a split section carry a "(Part N)" label.
        assert chunk.section_heading.startswith("Big Section")


def test_small_page_single_chunk_unsplit(wiki_dir):
    repo_root, wiki = wiki_dir
    text = (
        "---\ntitle: Small\ntype: concept\nsources: []\n---\n\n"
        "## Only\n\nA short body.\n"
    )
    page_path = _write_page(wiki, "small.md", text)
    page = load_wiki_page(page_path, repo_root)
    chunks = build_chunks_for_page(page)

    assert len(chunks) == 1
    assert chunks[0].section_heading == "Only"  # no "(Part N)" suffix
    assert "A short body." in chunks[0].text


def test_chunk_metadata_carries_expected_fields(wiki_dir):
    repo_root, wiki = wiki_dir
    text = (
        "---\ntitle: Meta Page\ntype: source\nsources:\n  - raw/sources/y.md\n---\n\n"
        "## S\n\nbody\n"
    )
    page_path = _write_page(wiki, "meta.md", text)
    page = load_wiki_page(page_path, repo_root)
    chunk = build_chunks_for_page(page)[0]

    assert chunk.metadata["title"] == "Meta Page"
    assert chunk.metadata["type"] == "source"
    assert chunk.metadata["path"] == "wiki/meta.md"
    assert chunk.metadata["sha256"] == page.sha256
    # sources is JSON-encoded in metadata.
    assert chunk.metadata["sources"] == '["raw/sources/y.md"]'
