"""Best-effort resolver for read-only SharePoint links to cited source pages.

Citations carry a wiki-page relative path (e.g. ``wiki/concepts/etc.md``) but no
URL. This resolver turns that path into a browser-openable SharePoint link by
resolving the document library's root ``webUrl`` once (via Microsoft Graph) and
appending the URL-encoded relative path.

It is intentionally fail-soft: if SharePoint is not configured or unreachable,
``link_for`` returns ``None`` and the caller renders the source as plain text.
An answer must never fail because a link could not be built.
"""

from __future__ import annotations

import logging
from urllib.parse import quote


LOGGER = logging.getLogger(__name__)


class SourceLinkResolver:
    """Resolve SharePoint links for source relative-paths, caching the drive base."""

    def __init__(self) -> None:
        self._base_url: str | None = None
        self._attempted = False

    def _drive_base(self) -> str | None:
        """Resolve (once) the document library's browser root URL, or None."""

        if self._attempted:
            return self._base_url
        self._attempted = True
        try:
            # Imported lazily so the bot has no hard dependency on the ingest stack
            # or SharePoint configuration at import time.
            from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
            from packages.wiki_core.settings import CoreSettings

            adapter = SharePointSourceSyncAdapter(CoreSettings.from_env())
            self._base_url = adapter.drive_web_url().rstrip("/")
            LOGGER.info("Resolved SharePoint source-link base: %s", self._base_url)
        except Exception:
            LOGGER.warning(
                "Could not resolve SharePoint drive webUrl; sources will render without links",
                exc_info=True,
            )
            self._base_url = None
        return self._base_url

    def link_for(self, relative_path: str) -> str | None:
        """Return a browser link for a drive-relative path, or None if unavailable."""

        rel = (relative_path or "").strip().strip("/")
        if not rel:
            return None
        base = self._drive_base()
        if not base:
            return None
        return f"{base}/{quote(rel, safe='/')}"
