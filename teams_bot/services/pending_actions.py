"""In-memory store of destructive admin actions awaiting confirmation.

A destructive command (``/remove``) first shows a preview card with Confirm /
Cancel buttons; the actual action runs only when the *same* admin confirms
within the TTL. Single App Service instance means an in-memory dict is
sufficient, and expiry-on-restart is a safe failure mode (the stale card's
Confirm simply reports that the request expired).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# How long a pending action stays confirmable.
_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class PendingAction:
    token: str
    command: str
    payload: dict[str, Any]
    initiator_aad_object_id: str
    initiator_name: str | None
    conversation_id: str | None
    created_at: float


@dataclass
class PendingActionStore:
    ttl_seconds: float = _TTL_SECONDS
    # Monotonic clock injectable for tests.
    _clock: Any = field(default=time.monotonic)
    _actions: dict[str, PendingAction] = field(default_factory=dict)

    def create(
        self,
        *,
        command: str,
        payload: dict[str, Any],
        initiator_aad_object_id: str,
        initiator_name: str | None,
        conversation_id: str | None,
    ) -> PendingAction:
        self._evict_expired()
        action = PendingAction(
            token=uuid.uuid4().hex,
            command=command,
            payload=dict(payload),
            initiator_aad_object_id=initiator_aad_object_id,
            initiator_name=initiator_name,
            conversation_id=conversation_id,
            created_at=self._clock(),
        )
        self._actions[action.token] = action
        return action

    def pop(self, token: str) -> PendingAction | None:
        """Remove and return the action, or None if missing/expired (single-use)."""
        self._evict_expired()
        return self._actions.pop(token, None)

    def put_back(self, action: PendingAction) -> None:
        """Restore an action popped by a non-initiator so the initiator can still act."""
        if (self._clock() - action.created_at) <= self.ttl_seconds:
            self._actions[action.token] = action

    def _evict_expired(self) -> None:
        now = self._clock()
        expired = [t for t, a in self._actions.items() if (now - a.created_at) > self.ttl_seconds]
        for token in expired:
            self._actions.pop(token, None)
