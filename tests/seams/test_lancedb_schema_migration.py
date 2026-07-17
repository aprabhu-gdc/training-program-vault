"""Self-healing of the legacy variable-length vector column.

Reproduces the production bug: an old table whose ``vector`` column is a
variable-length list (created before the fixed-size-list fix), which
``.search()`` rejects with "There is no vector column in the data". Opening the
store should migrate it in place (no re-embedding) so queries work again.
"""

from __future__ import annotations

import lancedb
import pyarrow as pa
import pytest

from packages.wiki_core.retrieval.lancedb_adapter import (
    IndexNotReadyError,
    LanceDbVectorStore,
)
from tests.conftest import make_core_settings


# Variable-length vector column — the broken production schema.
_LEGACY_SCHEMA = pa.schema(
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


def _row(row_id: str, path: str, vector: list[float]) -> dict:
    return {
        "id": row_id,
        "path": path,
        "title": row_id.title(),
        "type": "source",
        "section": "Overview",
        "chunk_index": 0,
        "sha256": "0" * 64,
        "sources": "[]",
        "text": f"content of {row_id}",
        "vector": vector,
    }


def _create_legacy_table(settings, rows: list[dict] | None) -> None:
    """Create a table with the broken variable-length vector column on disk."""
    db = lancedb.connect(str(settings.vector_db_path))
    if rows:
        db.create_table(settings.vector_table_name, data=rows, schema=_LEGACY_SCHEMA, mode="overwrite")
    else:
        db.create_table(settings.vector_table_name, schema=_LEGACY_SCHEMA, mode="overwrite")


def test_open_migrates_legacy_variable_length_vector_column(tmp_path):
    settings = make_core_settings(tmp_path)
    _create_legacy_table(
        settings,
        [
            _row("a", "wiki/sources/a.md", [1.0, 0.0, 0.0]),
            _row("b", "wiki/sources/b.md", [0.0, 1.0, 0.0]),
            _row("c", "wiki/sources/c.md", [0.0, 0.0, 1.0]),
        ],
    )

    store = LanceDbVectorStore(settings)

    assert pa.types.is_fixed_size_list(store._table.schema.field("vector").type)
    assert store._table.count_rows() == 3  # no rows lost, no re-embedding
    assert store.is_ready()

    results = store.search([1.0, 0.0, 0.0], top_k=3)
    assert len(results) == 3
    assert results[0]["id"] == "a"


def test_migration_is_idempotent(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _create_legacy_table(settings, [_row("a", "wiki/sources/a.md", [1.0, 0.0, 0.0])])

    LanceDbVectorStore(settings)  # first open migrates

    def _fail_if_called(self, table):  # pragma: no cover - only runs on regression
        raise AssertionError("schema migration should not run on an already-fixed table")

    monkeypatch.setattr(LanceDbVectorStore, "_migrate_vector_schema", _fail_if_called)
    store = LanceDbVectorStore(settings)  # second open must not re-migrate
    assert pa.types.is_fixed_size_list(store._table.schema.field("vector").type)
    assert store.search([1.0, 0.0, 0.0], top_k=1)


def test_infer_vector_dim_rejects_empty_and_ragged():
    # Consistent dims -> the dimension; empty/ragged -> None (unmigratable).
    consistent = pa.table({"vector": pa.array([[1.0, 0.0], [0.0, 1.0]], pa.list_(pa.float32()))})
    ragged = pa.table({"vector": pa.array([[1.0, 0.0], [1.0]], pa.list_(pa.float32()))})
    empty = pa.table({"vector": pa.array([], pa.list_(pa.float32()))})

    assert LanceDbVectorStore._infer_vector_dim(consistent) == 2
    assert LanceDbVectorStore._infer_vector_dim(ragged) is None
    assert LanceDbVectorStore._infer_vector_dim(empty) is None


def test_migration_failure_never_destroys_data(tmp_path, monkeypatch):
    settings = make_core_settings(tmp_path)
    _create_legacy_table(
        settings,
        [
            _row("a", "wiki/sources/a.md", [1.0, 0.0, 0.0]),
            _row("b", "wiki/sources/b.md", [0.0, 1.0, 0.0]),
        ],
    )

    # Force the "cannot determine dimension" branch: the table must be left
    # untouched (data preserved), not dropped or emptied.
    monkeypatch.setattr(LanceDbVectorStore, "_infer_vector_dim", staticmethod(lambda data: None))

    store = LanceDbVectorStore(settings)

    assert store._table is not None
    assert not pa.types.is_fixed_size_list(store._table.schema.field("vector").type)
    assert store._table.count_rows() == 2


def test_empty_legacy_table_is_dropped(tmp_path):
    settings = make_core_settings(tmp_path)
    _create_legacy_table(settings, rows=None)

    store = LanceDbVectorStore(settings)

    assert store._table is None
    assert not store.is_ready()


def test_rebuild_with_no_rows_drops_table(tmp_path):
    settings = make_core_settings(tmp_path)
    store = LanceDbVectorStore(settings)
    store.rebuild([_row("a", "wiki/sources/a.md", [1.0, 0.0, 0.0])])
    assert store.is_ready()

    store.rebuild([])  # empty rebuild must not leave a dimensionless table behind
    assert store._table is None
    assert not store.is_ready()


def test_search_without_table_raises_index_not_ready(tmp_path):
    settings = make_core_settings(tmp_path)
    store = LanceDbVectorStore(settings)  # nothing built yet

    assert store._table is None
    with pytest.raises(IndexNotReadyError):
        store.search([1.0, 0.0, 0.0], top_k=1)
