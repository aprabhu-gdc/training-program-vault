"""In-process memory of the most recently attached image(s) per conversation.

The bot keeps its short-term *text* history in ``ConversationState`` (which is
JSON-serializable and deliberately text-only). Images are large base64 blobs
that don't belong in that state, so the most recent image attachments are held
here in plain process memory instead. This lets a follow-up turn ("what was in
the photo?") re-send the actual image to the vision model.

Lifetime matches the rest of the bot's short-term memory: process-local (lost on
restart, single App Service instance assumed) and reset after the same idle gap
as the text history (``session_idle_minutes``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from packages.contracts.query import QueryAttachment


# Cap on how many recent images to carry forward, bounding the vision payload
# re-sent on each follow-up turn while an image is "in view".
_MAX_IMAGES = 2


@dataclass
class _Entry:
    attachments: tuple[QueryAttachment, ...]
    updated_at: float


@dataclass
class SessionImageStore:
    """Per-conversation store of recently shared images (newest kept)."""

    max_images: int = _MAX_IMAGES
    _entries: dict[str, _Entry] = field(default_factory=dict)

    def remember(self, key: str, attachments: list[QueryAttachment], *, now: float | None = None) -> None:
        """Replace the remembered images for ``key`` with the newest image attachments.

        Non-image attachments are ignored; nothing is stored if there are none.
        """

        images = [attachment for attachment in attachments if attachment.image_data_url]
        if not images:
            return
        images = images[-self.max_images :]
        self._entries[key] = _Entry(
            attachments=tuple(images),
            updated_at=now if now is not None else time.time(),
        )

    def recent(self, key: str, *, idle_seconds: float, now: float | None = None) -> list[QueryAttachment]:
        """Return the remembered images for ``key``, or [] if none / the session went idle.

        On a live hit the entry's timestamp is refreshed so the image stays
        available for as long as the conversation keeps going (mirrors how the
        text history refreshes ``updated_at`` on every turn).
        """

        entry = self._entries.get(key)
        if entry is None:
            return []
        current = now if now is not None else time.time()
        if idle_seconds > 0 and (current - entry.updated_at) > idle_seconds:
            self._entries.pop(key, None)
            return []
        entry.updated_at = current
        return list(entry.attachments)

    def clear(self, key: str) -> None:
        self._entries.pop(key, None)
