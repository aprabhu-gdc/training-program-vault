"""Pending admin-action store: TTL, single-use, and initiator binding."""

from __future__ import annotations

from teams_bot.services.pending_actions import PendingActionStore


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _store(clock: _Clock) -> PendingActionStore:
    return PendingActionStore(ttl_seconds=300.0, _clock=clock)


def _create(store: PendingActionStore, initiator: str = "admin-1"):
    return store.create(
        command="remove",
        payload={"path": "wiki/sources/foo.md"},
        initiator_aad_object_id=initiator,
        initiator_name="Dana",
        conversation_id="conv-1",
    )


def test_create_then_pop_is_single_use():
    store = _store(_Clock())
    action = _create(store)
    assert store.pop(action.token) is action
    assert store.pop(action.token) is None  # already consumed


def test_expired_action_is_not_returned():
    clock = _Clock()
    store = _store(clock)
    action = _create(store)
    clock.t = 301.0
    assert store.pop(action.token) is None


def test_put_back_restores_for_initiator():
    store = _store(_Clock())
    action = _create(store)
    popped = store.pop(action.token)
    store.put_back(popped)  # a non-initiator tried to confirm; restore it
    assert store.pop(action.token) is action


def test_put_back_after_expiry_is_a_noop():
    clock = _Clock()
    store = _store(clock)
    action = _create(store)
    popped = store.pop(action.token)
    clock.t = 400.0
    store.put_back(popped)
    assert store.pop(action.token) is None
