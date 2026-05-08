"""Compatibility wrapper for the extracted indexing service."""

from packages.wiki_core.retrieval.index_service import IndexingReport, VaultIndexer, main


__all__ = ["IndexingReport", "VaultIndexer", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
