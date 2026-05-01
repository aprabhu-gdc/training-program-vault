"""Chunk and index the `wiki/` directory into a local LanceDB table."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import lancedb
import pyarrow as pa

from rag_backend.config import BackendSettings
from rag_backend.llm import embed_texts_sync
from rag_backend.markdown import build_chunks_for_page, iter_wiki_markdown_files, load_wiki_page


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexingReport:
    mode: str
    indexed_files: list[str]
    deleted_files: list[str]
    chunk_count: int


class VaultIndexer:
    """Maintain a LanceDB index backed only by `wiki/` markdown pages."""

    TABLE_SCHEMA = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("path", pa.string()),
            pa.field("title", pa.string()),
            pa.field("type", pa.string()),
            pa.field("section", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("sha256", pa.string()),
            pa.field("sources", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32())),
        ]
    )

    def __init__(self, settings: BackendSettings | None = None) -> None:
        self._settings = settings or BackendSettings.from_env()
        self._settings.ensure_data_dirs()
        self._settings.validate_llm()
        self._db = lancedb.connect(str(self._settings.vector_db_path))
        self._table = self._open_table()

    @property
    def settings(self) -> BackendSettings:
        return self._settings

    def build(self) -> IndexingReport:
        """Rebuild the whole LanceDB table from the `wiki/` directory."""

        wiki_files = [path for path in iter_wiki_markdown_files(self._settings.wiki_root)]
        rows, manifest, indexed_files = self._rows_for_files(wiki_files)
        if rows:
            self._table = self._db.create_table(
                self._settings.vector_table_name,
                data=rows,
                mode="overwrite",
            )
        else:
            self._table = self._db.create_table(
                self._settings.vector_table_name,
                schema=self.TABLE_SCHEMA,
                mode="overwrite",
            )
        self._save_manifest(manifest)
        return IndexingReport(
            mode="build",
            indexed_files=indexed_files,
            deleted_files=[],
            chunk_count=len(rows),
        )

    def upsert_modified_files(self, changed_paths: Iterable[Path] | None = None) -> IndexingReport:
        """Upsert only modified wiki files and delete removed wiki files."""

        if self._table is None:
            return self.build()

        if self._table.count_rows() == 0:
            return self.build()

        manifest = self._load_manifest()
        existing_files = {
            path.relative_to(self._settings.repo_root).as_posix(): path
            for path in iter_wiki_markdown_files(self._settings.wiki_root)
        }

        changed_relative_paths: set[str] = set()
        if changed_paths is None:
            for relative_path, path in existing_files.items():
                sha256 = load_wiki_page(path, self._settings.repo_root).sha256
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
            self._delete_paths(deleted_paths)
            for relative_path in deleted_paths:
                manifest.pop(relative_path, None)

        changed_files = [existing_files[path] for path in sorted(changed_relative_paths) if path in existing_files]
        report = self._upsert_files(changed_files, mode="upsert")
        for path in changed_files:
            page = load_wiki_page(path, self._settings.repo_root)
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
            page = load_wiki_page(path, self._settings.repo_root)
            indexed_files.append(page.relative_path)
            manifest[page.relative_path] = page.sha256
            self._delete_paths([page.relative_path])
            chunks = build_chunks_for_page(page)
            for chunk in chunks:
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

        embeddings = embed_texts_sync([row["text"] for row in rows], self._settings)
        for row, embedding in zip(rows, embeddings):
            row["vector"] = [float(value) for value in embedding]

        self._ensure_table()
        self._table.add(rows)
        self._save_manifest(manifest)
        return IndexingReport(
            mode=mode,
            indexed_files=indexed_files,
            deleted_files=[],
            chunk_count=len(rows),
        )

    def _delete_paths(self, relative_paths: list[str]) -> None:
        if self._table is None:
            return
        for relative_path in relative_paths:
            try:
                escaped = relative_path.replace("'", "''")
                self._table.delete(f"path = '{escaped}'")
            except Exception:
                LOGGER.debug("Vector delete failed for path=%s", relative_path, exc_info=True)

    def _ensure_table(self):
        if self._table is None:
            self._table = self._db.create_table(
                self._settings.vector_table_name,
                schema=self.TABLE_SCHEMA,
                mode="overwrite",
            )
        return self._table

    def _open_table(self):
        try:
            response = self._db.list_tables()
            table_names = set(getattr(response, "tables", []) or [])
        except Exception:
            LOGGER.debug("Failed to list LanceDB tables", exc_info=True)
            return None
        if self._settings.vector_table_name not in table_names:
            return None
        try:
            return self._db.open_table(self._settings.vector_table_name)
        except Exception:
            LOGGER.warning("Failed to open LanceDB table; forcing rebuild on next use", exc_info=True)
            return None

    def _rows_for_files(
        self,
        files: list[Path],
    ) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
        rows: list[dict[str, Any]] = []
        manifest: dict[str, str] = {}
        indexed_files: list[str] = []

        for path in files:
            page = load_wiki_page(path, self._settings.repo_root)
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
            embeddings = embed_texts_sync([row["text"] for row in rows], self._settings)
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
        self._settings.vector_manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Index the training vault wiki into LanceDB.")
    parser.add_argument(
        "--mode",
        choices=("build", "upsert"),
        default="build",
        help="Rebuild all wiki files or upsert only modified wiki files.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional wiki file paths to upsert explicitly.",
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
