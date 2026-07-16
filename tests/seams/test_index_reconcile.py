"""VaultIndexer.reconcile heals index drift against a real tmp LanceDB table."""

from __future__ import annotations

from pathlib import Path

from packages.wiki_core.retrieval.index_service import VaultIndexer
from tests.conftest import make_core_settings


class _FakeEmbedder:
    """Records every embed call so tests can assert which chunks were embedded."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_texts_sync(self, texts):
        self.calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


def _page(settings, folder: str, slug: str, title: str, body: str = "Body text.") -> Path:
    directory = settings.wiki_root / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(
        f"---\ntitle: {title}\ntype: {'concept' if folder == 'concepts' else 'source'}\n---\n\n## Overview\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _indexer(settings) -> tuple[VaultIndexer, _FakeEmbedder]:
    indexer = VaultIndexer(settings)
    embedder = _FakeEmbedder()
    indexer._model_gateway = embedder  # noqa: SLF001 - inject fake embedder
    return indexer, embedder


def test_reconcile_indexes_only_new_pages(tmp_path):
    settings = make_core_settings(tmp_path)
    _page(settings, "sources", "a", "A")
    _page(settings, "sources", "b", "B")
    indexer, embedder = _indexer(settings)

    indexer.build()
    assert len(embedder.calls) == 1  # all pages embedded during build

    _page(settings, "concepts", "c", "C Concept")
    report = indexer.reconcile()

    assert report.indexed_files == ["wiki/concepts/c.md"]
    # Only the new page's chunks were embedded, not a and b again.
    assert len(embedder.calls) == 2
    manifest = indexer._load_manifest()  # noqa: SLF001
    assert "wiki/concepts/c.md" in manifest


def test_reconcile_removes_deleted_pages(tmp_path):
    settings = make_core_settings(tmp_path)
    _page(settings, "sources", "a", "A")
    page_b = _page(settings, "sources", "b", "B")
    indexer, _ = _indexer(settings)
    indexer.build()

    page_b.unlink()
    report = indexer.reconcile()

    assert "wiki/sources/b.md" in report.deleted_files
    assert "wiki/sources/b.md" not in indexer._load_manifest()  # noqa: SLF001


def test_second_reconcile_is_a_noop(tmp_path):
    settings = make_core_settings(tmp_path)
    _page(settings, "sources", "a", "A")
    indexer, embedder = _indexer(settings)
    indexer.build()
    embedder.calls.clear()

    report = indexer.reconcile()
    assert report.indexed_files == []
    assert report.deleted_files == []
    assert embedder.calls == []  # nothing changed -> no embedding call


def test_reconcile_skips_unreadable_page(tmp_path):
    settings = make_core_settings(tmp_path)
    _page(settings, "sources", "a", "A")
    indexer, _ = _indexer(settings)
    indexer.build()

    _page(settings, "concepts", "c", "C Concept")
    # A file that raises on read must not abort the reconcile of the rest.
    (settings.wiki_root / "sources" / "broken.md").write_bytes(b"\xff\xfe\x00broken")

    report = indexer.reconcile()
    assert "wiki/concepts/c.md" in report.indexed_files
