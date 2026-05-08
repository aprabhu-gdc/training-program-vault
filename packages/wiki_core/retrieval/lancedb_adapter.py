"""LanceDB-backed vector store adapter for local development compatibility."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import lancedb
import pyarrow as pa

from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)


class LanceDbVectorStore:
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

    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.ensure_data_dirs()
        self._db = lancedb.connect(str(self._settings.vector_db_path))
        self._table = self._open_table()

    def is_ready(self) -> bool:
        return self._table is not None and self._table.count_rows() > 0

    def rebuild(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._table = self._db.create_table(self._settings.vector_table_name, data=rows, mode="overwrite")
        else:
            self._table = self._db.create_table(self._settings.vector_table_name, schema=self.TABLE_SCHEMA, mode="overwrite")

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self._ensure_table().add(rows)

    def delete_by_paths(self, relative_paths: Iterable[str]) -> None:
        if self._table is None:
            return
        for relative_path in relative_paths:
            try:
                escaped = str(relative_path).replace("'", "''")
                self._table.delete(f"path = '{escaped}'")
            except Exception:
                LOGGER.debug("Vector delete failed for path=%s", relative_path, exc_info=True)

    def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if filters:
            LOGGER.debug("Ignoring LanceDB filters in compatibility adapter: %s", filters)
        table = self._ensure_table()
        return table.search(embedding).limit(top_k).to_list()

    def _ensure_table(self):
        if self._table is None:
            self._table = self._db.create_table(self._settings.vector_table_name, schema=self.TABLE_SCHEMA, mode="overwrite")
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
