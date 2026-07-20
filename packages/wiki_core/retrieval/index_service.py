"""Index wiki pages into a vector store using wiki core abstractions."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from packages.wiki_core.ai.legacy_provider_gateway import LegacyProviderGateway
from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import build_chunks_for_page
from packages.wiki_core.retrieval.lancedb_adapter import LanceDbVectorStore
from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexingReport:
    mode: str
    indexed_files: list[str]
    deleted_files: list[str]
    chunk_count: int


class VaultIndexer:
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.ensure_data_dirs()
        self._settings.validate_llm()
        self._page_store = FilePageStore(self._settings)
        self._model_gateway = LegacyProviderGateway(self._settings)
        self._vector_store = LanceDbVectorStore(self._settings)

    @property
    def settings(self) -> CoreSettings:
        return self._settings

    def build(self) -> IndexingReport:
        wiki_files = self._page_store.iter_wiki_pages()
        rows, manifest, indexed_files = self._rows_for_files(wiki_files)
        self._vector_store.rebuild(rows)
        self._save_manifest(manifest)
        return IndexingReport(mode="build", indexed_files=indexed_files, deleted_files=[], chunk_count=len(rows))

    def reconcile(self) -> IndexingReport:
        """Bring the index into line with every wiki page on disk.

        Diffs all wiki pages against the manifest and embeds only missing or
        changed pages (and removes vanished ones), so it heals index drift —
        e.g. pages pulled from SharePoint that were never handed to the indexer.
        Cheap when nothing changed (sha diff only, no embedding calls).
        """

        report = self.upsert_modified_files(changed_paths=None)
        LOGGER.info(
            "Index reconcile complete indexed=%d deleted=%d chunks=%d",
            len(report.indexed_files),
            len(report.deleted_files),
            report.chunk_count,
        )
        return report

    def delete_page(self, relative_path: str) -> bool:
        """Remove one page's vectors and manifest entry. No embedding calls.

        Returns True when the manifest had an entry for the page (i.e. it was
        indexed). Safe to call for a page that was never indexed.
        """
        self._vector_store.delete_by_paths([relative_path])
        manifest = self._load_manifest()
        existed = manifest.pop(relative_path, None) is not None
        self._save_manifest(manifest)
        return existed

    def upsert_modified_files(self, changed_paths: Iterable[Path] | None = None) -> IndexingReport:
        manifest = self._load_manifest()
        existing_files = {
            path.relative_to(self._settings.repo_root).as_posix(): path
            for path in self._page_store.iter_wiki_pages()
        }

        changed_relative_paths: set[str] = set()
        if changed_paths is None:
            for relative_path, path in existing_files.items():
                try:
                    sha256 = self._page_store.load_wiki_page(path).sha256
                except Exception:
                    # One unreadable page (e.g. a partially-synced file) must not
                    # abort a full reconcile of every other page.
                    LOGGER.warning("Skipping unreadable wiki page during reconcile: %s", path, exc_info=True)
                    continue
                if manifest.get(relative_path) != sha256:
                    changed_relative_paths.add(relative_path)
        else:
            for raw_path in changed_paths:
                path = raw_path.resolve()
                try:
                    relative_path = path.relative_to(self._settings.repo_root).as_posix()
                except ValueError:
                    continue
                if relative_path.startswith("wiki/"):
                    changed_relative_paths.add(relative_path)

        deleted_paths = sorted(set(manifest) - set(existing_files))
        if deleted_paths:
            self._vector_store.delete_by_paths(deleted_paths)
            for relative_path in deleted_paths:
                manifest.pop(relative_path, None)

        changed_files = [existing_files[path] for path in sorted(changed_relative_paths) if path in existing_files]
        report = self._upsert_files(changed_files, mode="upsert")
        for path in changed_files:
            page = self._page_store.load_wiki_page(path)
            manifest[page.relative_path] = page.sha256
        self._save_manifest(manifest)

        return IndexingReport(
            mode="upsert",
            indexed_files=report.indexed_files,
            deleted_files=deleted_paths,
            chunk_count=report.chunk_count,
        )

    def _upsert_files(self, files: list[Path], mode: str) -> IndexingReport:
        if not files:
            return IndexingReport(mode=mode, indexed_files=[], deleted_files=[], chunk_count=0)

        rows: list[dict[str, Any]] = []
        manifest = self._load_manifest()
        indexed_files: list[str] = []

        for path in files:
            page = self._page_store.load_wiki_page(path)
            indexed_files.append(page.relative_path)
            manifest[page.relative_path] = page.sha256
            self._vector_store.delete_by_paths([page.relative_path])
            for chunk in build_chunks_for_page(page):
                rows.append(
                    {
                        "id": chunk.chunk_id,
                        "path": chunk.relative_path,
                        "title": chunk.title,
                        "type": chunk.page_type,
                        "section": chunk.section_heading,
                        "chunk_index": int(chunk.metadata.get("chunk_index", 0)),
                        "sha256": str(chunk.metadata.get("sha256", "")),
                        "sources": str(chunk.metadata.get("sources", "[]")),
                        "text": chunk.text,
                        "vector": [],
                    }
                )

        if not rows:
            self._save_manifest(manifest)
            return IndexingReport(mode=mode, indexed_files=indexed_files, deleted_files=[], chunk_count=0)

        embeddings = self._model_gateway.embed_texts_sync([row["text"] for row in rows])
        for row, embedding in zip(rows, embeddings):
            row["vector"] = [float(value) for value in embedding]

        self._vector_store.upsert(rows)
        self._save_manifest(manifest)
        return IndexingReport(mode=mode, indexed_files=indexed_files, deleted_files=[], chunk_count=len(rows))

    def _rows_for_files(self, files: list[Path]) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
        rows: list[dict[str, Any]] = []
        manifest: dict[str, str] = {}
        indexed_files: list[str] = []

        for path in files:
            page = self._page_store.load_wiki_page(path)
            manifest[page.relative_path] = page.sha256
            indexed_files.append(page.relative_path)
            for chunk in build_chunks_for_page(page):
                rows.append(
                    {
                        "id": chunk.chunk_id,
                        "path": chunk.relative_path,
                        "title": chunk.title,
                        "type": chunk.page_type,
                        "section": chunk.section_heading,
                        "chunk_index": int(chunk.metadata.get("chunk_index", 0)),
                        "sha256": str(chunk.metadata.get("sha256", "")),
                        "sources": str(chunk.metadata.get("sources", "[]")),
                        "text": chunk.text,
                        "vector": [],
                    }
                )

        if rows:
            embeddings = self._model_gateway.embed_texts_sync([row["text"] for row in rows])
            for row, embedding in zip(rows, embeddings):
                row["vector"] = [float(value) for value in embedding]

        return rows, manifest, indexed_files

    def _load_manifest(self) -> dict[str, str]:
        path = self._settings.vector_manifest_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Failed to read manifest at %s; starting fresh", path)
            return {}

    def _save_manifest(self, manifest: dict[str, str]) -> None:
        self._settings.vector_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Index the training vault wiki into the configured vector store.")
    parser.add_argument("--mode", choices=("build", "upsert"), default="build")
    parser.add_argument("paths", nargs="*", help="Optional wiki file paths to upsert explicitly.")
    args = parser.parse_args()

    indexer = VaultIndexer()
    if args.mode == "build":
        report = indexer.build()
    else:
        changed_paths = [Path(path).resolve() for path in args.paths] if args.paths else None
        report = indexer.upsert_modified_files(changed_paths=changed_paths)

    LOGGER.info(
        "Index complete mode=%s files=%s deleted=%s chunks=%s",
        report.mode,
        len(report.indexed_files),
        len(report.deleted_files),
        report.chunk_count,
    )
    return 0
