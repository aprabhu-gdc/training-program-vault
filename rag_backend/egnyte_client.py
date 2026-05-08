"""Compatibility wrapper for the extracted Egnyte adapter."""

from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.egnyte_adapter import EgnyteSourceSyncAdapter


EgnyteFileEvent = SourceFileEvent
EgnyteClient = EgnyteSourceSyncAdapter


__all__ = ["EgnyteClient", "EgnyteFileEvent"]
