"""Phase 09: SharePoint webhook parsing + the clientState security boundary.

Microsoft Graph calls are mocked: `_get_access_token` (no OAuth) and
`_fetch_drive_item` (no drive-item GET) are patched per test.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from tests.conftest import make_core_settings


ITEM = {
    "id": "item-1",
    "name": "topic.docx",
    "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "parentReference": {"path": "/drives/abc/root:/raw/sources"},
    "lastModifiedDateTime": "2026-06-22T10:00:00Z",
}


def _adapter(tmp_path, monkeypatch, *, client_state="secret", item=ITEM):
    settings = make_core_settings(tmp_path, sharepoint_webhook_client_state=client_state)
    adapter = SharePointSourceSyncAdapter(settings)
    monkeypatch.setattr(adapter, "_get_access_token", lambda: "fake-token")
    fetch = Mock(return_value=item)
    monkeypatch.setattr(adapter, "_fetch_drive_item", fetch)
    return adapter, fetch


def test_valid_client_state_yields_webhook_event(tmp_path, monkeypatch):
    adapter, fetch = _adapter(tmp_path, monkeypatch, client_state="secret")
    payload = {"value": [{"clientState": "secret", "resourceData": {"id": "item-1"}}]}

    events = adapter.parse_webhook_payload(payload)

    assert len(events) == 1
    event = events[0]
    assert event.path == "raw/sources/topic.docx"
    assert event.event_type == "webhook"
    assert event.entry_id == "item-1"
    assert event.modified_at == "2026-06-22T10:00:00Z"
    fetch.assert_called_once()


def test_mismatched_client_state_is_rejected(tmp_path, monkeypatch):
    """Security boundary: a forged/mismatched clientState yields no events and
    must not even resolve the drive item."""
    adapter, fetch = _adapter(tmp_path, monkeypatch, client_state="secret")
    payload = {"value": [{"clientState": "WRONG", "resourceData": {"id": "item-1"}}]}

    events = adapter.parse_webhook_payload(payload)

    assert events == []
    fetch.assert_not_called()


def test_missing_client_state_is_rejected_when_secret_configured(tmp_path, monkeypatch):
    adapter, fetch = _adapter(tmp_path, monkeypatch, client_state="secret")
    payload = {"value": [{"resourceData": {"id": "item-1"}}]}  # no clientState at all

    assert adapter.parse_webhook_payload(payload) == []
    fetch.assert_not_called()


def test_empty_configured_client_state_skips_validation(tmp_path, monkeypatch):
    """Documents current behavior: when no shared secret is configured, the
    adapter does not enforce clientState and accepts the notification."""
    adapter, _ = _adapter(tmp_path, monkeypatch, client_state="")
    payload = {"value": [{"clientState": "anything", "resourceData": {"id": "item-1"}}]}

    events = adapter.parse_webhook_payload(payload)
    assert len(events) == 1


def test_item_without_file_property_is_skipped(tmp_path, monkeypatch):
    folder_item = {**ITEM, "file": None, "folder": {}}
    adapter, _ = _adapter(tmp_path, monkeypatch, client_state="secret", item=folder_item)
    payload = {"value": [{"clientState": "secret", "resourceData": {"id": "item-1"}}]}

    assert adapter.parse_webhook_payload(payload) == []


def test_missing_drive_item_is_skipped(tmp_path, monkeypatch):
    adapter, _ = _adapter(tmp_path, monkeypatch, client_state="secret", item=None)
    payload = {"value": [{"clientState": "secret", "resourceData": {"id": "gone"}}]}

    assert adapter.parse_webhook_payload(payload) == []


@pytest.mark.parametrize("payload", [None, "not-a-dict", {}, {"value": "nope"}, {"value": []}])
def test_malformed_payloads_yield_no_events(tmp_path, monkeypatch, payload):
    adapter, _ = _adapter(tmp_path, monkeypatch, client_state="secret")
    assert adapter.parse_webhook_payload(payload) == []
