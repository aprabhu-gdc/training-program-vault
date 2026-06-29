"""Phase 09: Service Bus consumer lock-loss handling.

The azure-servicebus client and AutoLockRenewer are faked so no real broker is
contacted; only the completion/lock-loss control flow is exercised.
"""

from __future__ import annotations

import json

import azure.servicebus as azure_sb
import pytest
from azure.servicebus.exceptions import MessageLockLostError

import packages.shared.messaging.service_bus as sb


class FakeMessage:
    def __init__(self, payload: dict, message_id: str = "m1"):
        self.body = [json.dumps(payload).encode("utf-8")]
        self.message_id = message_id


class FakeReceiver:
    def __init__(self, messages, *, complete_raises=None):
        self._messages = messages
        self._complete_raises = complete_raises
        self.completed = []
        self.abandoned = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def receive_messages(self, max_message_count=1, max_wait_time=5):
        return list(self._messages)

    def complete_message(self, message):
        if self._complete_raises is not None:
            raise self._complete_raises
        self.completed.append(message)

    def abandon_message(self, message):
        self.abandoned.append(message)


class FakeClient:
    def __init__(self, receiver):
        self._receiver = receiver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_queue_receiver(self, queue_name, max_wait_time=5):
        return self._receiver


class FakeRenewer:
    def __init__(self, *args, **kwargs):
        self.registered = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def register(self, *args, **kwargs):
        self.registered.append((args, kwargs))


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(azure_sb, "AutoLockRenewer", FakeRenewer)

    def install(receiver):
        monkeypatch.setattr(
            sb, "_create_service_bus_client", lambda **kwargs: FakeClient(receiver)
        )

    return install


def _run(receiver, *, treat_lock_loss):
    calls = []
    processed = sb.process_queue_messages(
        connection_string="conn",
        fully_qualified_namespace="",
        queue_name="q",
        processor=lambda payload: calls.append(payload),
        treat_completion_lock_loss_as_processed=treat_lock_loss,
    )
    return processed, calls


def test_happy_path_completes_and_counts(patched):
    receiver = FakeReceiver([FakeMessage({"job_id": "j", "job_type": "manual"})])
    patched(receiver)

    processed, calls = _run(receiver, treat_lock_loss=False)

    assert processed == 1
    assert calls == [{"job_id": "j", "job_type": "manual"}]
    assert len(receiver.completed) == 1


def test_lock_loss_swallowed_when_flag_true(patched):
    receiver = FakeReceiver(
        [FakeMessage({"job_id": "j", "job_type": "manual"})],
        complete_raises=MessageLockLostError(),
    )
    patched(receiver)

    processed, calls = _run(receiver, treat_lock_loss=True)

    # Processing succeeded; the post-processing lock loss is treated as done.
    assert processed == 1
    assert calls == [{"job_id": "j", "job_type": "manual"}]
    assert receiver.abandoned == []  # processor did not fail, so no abandon


def test_lock_loss_reraised_when_flag_false(patched):
    receiver = FakeReceiver(
        [FakeMessage({"job_id": "j", "job_type": "manual"})],
        complete_raises=MessageLockLostError(),
    )
    patched(receiver)

    with pytest.raises(MessageLockLostError):
        _run(receiver, treat_lock_loss=False)


def test_processor_failure_abandons_and_reraises(patched, monkeypatch):
    receiver = FakeReceiver([FakeMessage({"job_id": "j", "job_type": "manual"})])
    patched(receiver)

    def boom(payload):
        raise ValueError("processing failed")

    with pytest.raises(ValueError, match="processing failed"):
        sb.process_queue_messages(
            connection_string="conn",
            fully_qualified_namespace="",
            queue_name="q",
            processor=boom,
            treat_completion_lock_loss_as_processed=True,
        )
    assert len(receiver.abandoned) == 1
