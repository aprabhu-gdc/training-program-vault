"""LanceDB-backed vector store adapter for local development compatibility."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any

import lancedb
import pyarrow as pa
import pyarrow.compute as pc

from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)

# How long a migration lock file is trusted before it is treated as stale and
# reclaimed (a process that crashed mid-migration would otherwise block healing).
_MIGRATION_LOCK_STALE_SECONDS = 600


class IndexNotReadyError(RuntimeError):
    """Raised when the vector index cannot serve queries yet.

    Signals a *recoverable* state — the table does not exist, has no rows, or is
    being rebuilt — as opposed to an unexpected backend failure. Callers can use
    this to tell users "the index is being built" rather than a generic error.
    """


class LanceDbVectorStore:
    # Non-vector columns, shared by every table generation. The vector column is
    # NEVER declared here: it must always be a fixed-size list of a known
    # dimension (see _schema_for_vector_dim). A variable-length list column
    # silently breaks .search() with "There is no vector column in the data",
    # which is the bug this adapter guards against.
    _BASE_FIELDS = [
        pa.field("id", pa.string()),
        pa.field("path", pa.string()),
        pa.field("title", pa.string()),
        pa.field("type", pa.string()),
        pa.field("section", pa.string()),
        pa.field("chunk_index", pa.int32()),
        pa.field("sha256", pa.string()),
        pa.field("sources", pa.string()),
        pa.field("text", pa.string()),
    ]

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
        return pa.schema([*cls._BASE_FIELDS, pa.field("vector", pa.list_(pa.float32(), dim))])

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
            # No rows means we cannot know the vector dimension, so we must NOT
            # create a table: a dimensionless vector column is exactly the broken
            # schema that breaks search. Drop any existing table instead; is_ready()
            # then reports "not ready" until a real build/sync provides rows.
            self._drop_table_safe(name)
            self._table = None
            LOGGER.warning("Rebuilt LanceDB table '%s' with 0 rows: dropped table (empty index)", name)

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        name = self._settings.vector_table_name
        if self._table is None:
            # Create with an explicit fixed-size-list vector column (see rebuild());
            # an inferred variable-length list column is what .search() cannot use.
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
        # Never create a table at query time: doing so risks overwriting a table
        # that a concurrent sync is populating, and (historically) reintroduced the
        # broken variable-length vector schema. Re-open once in case a build/sync
        # created the table since construction, then fail loudly if still absent.
        if self._table is None:
            self._table = self._open_table()
        if self._table is None:
            raise IndexNotReadyError(
                f"LanceDB table '{self._settings.vector_table_name}' does not exist at "
                f"{self._settings.vector_db_path}; run an index build or /sync."
            )
        return self._table

    def _open_table(self):
        db_path = self._settings.vector_db_path
        name = self._settings.vector_table_name
        try:
            table_names = self._list_table_names()
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
        if not self._vector_column_is_fixed(table):
            table = self._migrate_vector_schema(table)
        return table

    def _list_table_names(self) -> set[str]:
        """Table names, tolerating both ListTablesResponse and plain-list clients."""
        response = self._db.list_tables()
        names = getattr(response, "tables", None)
        if names is None:
            names = list(response) if isinstance(response, (list, tuple, set)) else []
        return set(names)

    @staticmethod
    def _vector_column_is_fixed(table: Any) -> bool:
        """True if the table's vector column is already a fixed-size list.

        Returns True when the schema cannot be read or has no vector field, since
        migration cannot help those cases — only a genuine variable-length list
        column should trigger a migration.
        """
        try:
            return pa.types.is_fixed_size_list(table.schema.field("vector").type)
        except Exception:
            return True

    @staticmethod
    def _infer_vector_dim(data: "pa.Table") -> int | None:
        """Common vector length across rows, or None if empty/ragged/degenerate."""
        try:
            lengths = pc.list_value_length(data.column("vector")).drop_null()
        except Exception:
            return None
        if len(lengths) == 0:
            return None
        unique = pc.unique(lengths)
        if len(unique) != 1:
            return None
        dim = unique[0].as_py()
        if not dim or dim <= 0:
            return None
        return int(dim)

    def _migrate_vector_schema(self, table: Any) -> Any | None:
        """Rewrite a legacy variable-length vector column as a fixed-size list.

        Old tables were created with a variable-length list vector column that
        .search() rejects. The stored embeddings already have consistent
        dimensions, so the column can be cast in place with NO re-embedding.

        Returns the migrated table on success, None if an empty legacy table was
        dropped, or the original table unchanged if migration cannot proceed
        safely. Never destroys data on failure.
        """
        name = self._settings.vector_table_name
        lock_path = self._settings.vector_db_path / ".schema-migration.lock"
        lock_fd = self._acquire_migration_lock(lock_path)
        if lock_fd is None:
            LOGGER.info(
                "Skipping LanceDB schema migration for '%s': another process holds the "
                "migration lock. The healed table will be picked up on the next open.",
                name,
            )
            return table
        try:
            data = table.to_arrow()
            if data.num_rows == 0:
                LOGGER.warning(
                    "LanceDB table '%s' has a legacy variable-length vector column but 0 rows; "
                    "dropping it so the next index build/sync recreates it with the correct schema.",
                    name,
                )
                self._drop_table_safe(name)
                return None
            dim = self._infer_vector_dim(data)
            if dim is None:
                LOGGER.error(
                    "Cannot migrate LanceDB table '%s': vector column has empty or inconsistent "
                    "dimensions. Vector search will keep failing until you run "
                    "`python -m packages.wiki_core.retrieval.index_service --mode build`.",
                    name,
                )
                return table
            schema = self._schema_for_vector_dim(dim)
            casted = data.cast(schema)
            migrated = self._db.create_table(name, data=casted, schema=schema, mode="overwrite")
            LOGGER.info(
                "Migrated LanceDB table '%s' vector column to fixed_size_list[%d] (%d rows, no re-embedding)",
                name, dim, data.num_rows,
            )
            return migrated
        except Exception:
            LOGGER.error(
                "Failed to migrate LanceDB table '%s' vector column to a fixed-size list; "
                "leaving the table unchanged. Vector search will keep failing until you run "
                "`python -m packages.wiki_core.retrieval.index_service --mode build`.",
                name, exc_info=True,
            )
            return table
        finally:
            self._release_migration_lock(lock_fd, lock_path)

    @staticmethod
    def _acquire_migration_lock(lock_path) -> int | None:
        """Best-effort exclusive lock; returns an fd, or None if another holder is active."""
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(str(lock_path))
            except OSError:
                return None
            if age <= _MIGRATION_LOCK_STALE_SECONDS:
                return None
            # Stale lock from a crashed migration: reclaim it.
            try:
                os.unlink(str(lock_path))
                return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                return None
        except OSError:
            return None

    @staticmethod
    def _release_migration_lock(fd: int, lock_path) -> None:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass

    def _drop_table_safe(self, name: str) -> None:
        try:
            self._db.drop_table(name, ignore_missing=True)
        except TypeError:
            # Older/newer client without the ignore_missing kwarg.
            try:
                self._db.drop_table(name)
            except Exception:
                LOGGER.debug("drop_table('%s') failed", name, exc_info=True)
        except Exception:
            LOGGER.debug("drop_table('%s') failed", name, exc_info=True)
