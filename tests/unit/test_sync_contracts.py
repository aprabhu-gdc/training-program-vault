"""Phase 09: ingest contract uses plain strings (Literal narrowing removed)."""

from __future__ import annotations

import dataclasses

from packages.contracts.sync import SourceFileEvent, SyncJobMessage


def test_source_file_event_accepts_arbitrary_event_type_strings():
    # Phase 09 dropped Literal narrowing; both runtime event types must construct.
    manual = SourceFileEvent(path="raw/sources/a.docx", event_type="manual-sync")
    webhook = SourceFileEvent(path="raw/sources/b.pdf", event_type="webhook")
    assert manual.event_type == "manual-sync"
    assert webhook.event_type == "webhook"
    # A novel event type a future source could emit must also be accepted.
    other = SourceFileEvent(path="raw/sources/c.pptx", event_type="some-future-source")
    assert other.event_type == "some-future-source"


def test_source_file_event_optional_fields_default_none():
    event = SourceFileEvent(path="raw/sources/a.docx", event_type="webhook")
    assert event.modified_at is None
    assert event.entry_id is None


def test_event_type_field_is_plain_str_not_literal():
    field = {f.name: f for f in dataclasses.fields(SourceFileEvent)}["event_type"]
    # The annotation should be the bare `str`, not a typing.Literal[...].
    assert field.type in ("str", str)


def test_sync_job_message_carries_webhook_payload():
    job = SyncJobMessage(
        job_id="abc",
        job_type="webhook",
        payload={"path": "raw/sources/x.docx", "modified_at": "t", "entry_id": "id"},
        source="sharepoint-webhook",
    )
    assert job.job_type == "webhook"
    assert job.payload["path"] == "raw/sources/x.docx"
    assert job.source == "sharepoint-webhook"


def test_event_key_format_matches_worker_state_contract():
    # The worker/ingest service fingerprints events as
    # "{event_type}|{modified_at}|{entry_id}"; lock that shape down.
    event = SourceFileEvent(
        path="raw/sources/x.docx",
        event_type="webhook",
        modified_at="2026-06-22T00:00:00Z",
        entry_id="item-1",
    )
    key = f"{event.event_type}|{event.modified_at}|{event.entry_id}"
    assert key == "webhook|2026-06-22T00:00:00Z|item-1"
