"""Compatibility wrapper for the extracted ingest service."""

from __future__ import annotations

from packages.wiki_core.ingest.ingest_service import AutoIngestService, SyncReport, main


__all__ = ["AutoIngestService", "SyncReport", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
