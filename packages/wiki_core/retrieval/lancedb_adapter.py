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

    @staticmethod
    def _vector_column_type(table: Any) -> str:
        """Arrow type of the ``vector`` column, for diagnostics.

        Vector search requires a fixed-size-list column; surfacing the actual type in
        logs makes a wrong column type (the "no vector column" failure) obvious.
        """
        try:
            return str(table.schema.field("vector").type)
        except Exception:
            return "<unknown>"

    def is_ready(self) -> bool:
        if self._table is None:
            LOGGER.debug(
                "Vault index not ready: no LanceDB table '%s' at %s",
                self._settings.vector_table_name,
                self._settings.vector_db_path,
            )
            return False
        row_count = self._table.count_rows()
        if row_count == 0:
            LOGGER.warning(
                "Vault index table '%s' exists but has 0 rows; an index build/sync is needed",
                self._settings.vector_table_name,
            )
        return row_count > 0

    @classmethod
    def _schema_for_vector_dim(cls, dim: int) -> pa.Schema:
        # Force a fixed-size-list vector column of the given dimension. LanceDB vector
        # search REQUIRES a fixed-size-list; letting create_table() infer the type from
        # Python-list data is not reliable across environments and produced a
        # variable-length list column in production, which .search() rejects with
        # "There is no vector column in the data".
        return pa.schema(
            [
                pa.field("vector", pa.list_(pa.float32(), dim)) if field.name == "vector" else field
                for field in cls.TABLE_SCHEMA
            ]
        )

    def rebuild(self, rows: list[dict[str, Any]]) -> None:
        name = self._settings.vector_table_name
        if rows:
            dim = len(rows[0]["vector"])
            schema = self._schema_for_vector_dim(dim)
            self._table = self._db.create_table(name, data=rows, schema=schema, mode="overwrite")
            LOGGER.info(
                "Rebuilt LanceDB table '%s': %d rows, vector dim=%d, column type=%s",
                name, self._table.count_rows(), dim, self._vector_column_type(self._table),
            )
        else:
            self._table = self._db.create_table(name, schema=self.TABLE_SCHEMA, mode="overwrite")
            LOGGER.warning("Rebuilt LanceDB table '%s' with 0 rows (empty index)", name)

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        name = self._settings.vector_table_name
        if self._table is None:
            # Create with an explicit fixed-size-list vector column (see rebuild()); the
            # static TABLE_SCHEMA uses a variable-length list that .search() cannot use.
            dim = len(rows[0]["vector"])
            schema = self._schema_for_vector_dim(dim)
            self._table = self._db.create_table(name, data=rows, schema=schema, mode="overwrite")
            LOGGER.info(
                "Created LanceDB table '%s' from %d rows, vector dim=%d, column type=%s",
                name, len(rows), dim, self._vector_column_type(self._table),
            )
            return
        self._table.add(rows)
        LOGGER.info("Upserted %d rows into LanceDB table '%s' (now %d rows)", len(rows), name, self._table.count_rows())

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
        table = self._ensure_table()
        try:
            query = table.search(embedding)
            if filters:
                # Equality filters only (e.g. {"type": "concept"}), applied before
                # the vector search so top_k is honored within the filtered set.
                clause = " AND ".join(
                    f"{column} = '{str(value).replace(chr(39), chr(39) * 2)}'"
                    for column, value in filters.items()
                )
                query = query.where(clause, prefilter=True)
            results = query.limit(top_k).to_list()
        except Exception:
            LOGGER.error(
                "LanceDB vector search failed on table '%s' (vector column type=%s). Vector "
                "search requires a fixed-size-list column; a variable-length list column fails "
                "here — rebuild the index if the type above is not a fixed_size_list.",
                self._settings.vector_table_name, self._vector_column_type(table),
            )
            raise
        LOGGER.debug(
            "LanceDB search on '%s' returned %d results (top_k=%d)",
            self._settings.vector_table_name, len(results), top_k,
        )
        return results

    def _ensure_table(self):
        if self._table is None:
            LOGGER.warning(
                "LanceDB table '%s' missing at use time; creating an empty table. Queries will "
                "return nothing until an index build/sync runs.",
                self._settings.vector_table_name,
            )
            self._table = self._db.create_table(self._settings.vector_table_name, schema=self.TABLE_SCHEMA, mode="overwrite")
        return self._table

    def _open_table(self):
        db_path = self._settings.vector_db_path
        name = self._settings.vector_table_name
        try:
            response = self._db.list_tables()
            table_names = set(getattr(response, "tables", []) or [])
        except Exception:
            LOGGER.warning("Failed to list LanceDB tables at %s", db_path, exc_info=True)
            return None
        if name not in table_names:
            LOGGER.info(
                "LanceDB table '%s' not found at %s (index not built yet); tables present: %s",
                name, db_path, sorted(table_names),
            )
            return None
        try:
            table = self._db.open_table(name)
        except Exception:
            LOGGER.warning(
                "Failed to open LanceDB table '%s' at %s; forcing rebuild on next use",
                name, db_path, exc_info=True,
            )
            return None
        LOGGER.info(
            "Opened LanceDB table '%s' at %s (vector column type=%s)",
            name, db_path, self._vector_column_type(table),
        )
        return table
