"""SubscriptionManager decision logic (Graph adapter faked)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from packages.wiki_core.ingest.subscription_manager import (
    SubscriptionManager,
    parse_graph_datetime,
)


NOTIFY_URL = "https://bot.example.com/api/webhooks/sharepoint"
RESOURCE = "/drives/drive-1/root"


class FakeAdapter:
    def __init__(self, subscriptions=(), raise_on_list=False):
        self.subscriptions = list(subscriptions)
        self.raise_on_list = raise_on_list
        self.created = 0
        self.renewed: list[str] = []

    def subscription_resource(self):
        return RESOURCE

    def list_subscriptions(self):
        if self.raise_on_list:
            raise RuntimeError("graph down")
        return list(self.subscriptions)

    def create_subscription(self):
        self.created += 1
        return {"id": "new-sub", "expirationDateTime": "2099-01-01T00:00:00.0000000Z"}

    def renew_subscription(self, subscription_id):
        self.renewed.append(subscription_id)
        return {"id": subscription_id, "expirationDateTime": "2099-01-01T00:00:00.0000000Z"}


def _settings(url=NOTIFY_URL, client_state="secret"):
    return SimpleNamespace(
        sharepoint_webhook_notification_url=url,
        sharepoint_webhook_client_state=client_state,
    )


def _expiry(hours_from_now: float) -> str:
    stamp = datetime.now(UTC) + timedelta(hours=hours_from_now)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def _subscription(hours_from_now=20.0, url=NOTIFY_URL, resource=RESOURCE):
    return {
        "id": "sub-1",
        "notificationUrl": url,
        "resource": resource,
        "expirationDateTime": _expiry(hours_from_now),
    }


def test_unconfigured_skips_and_logs_once(caplog):
    adapter = FakeAdapter()
    manager = SubscriptionManager(lambda: adapter, _settings(url=""))
    assert manager.ensure_once() == "skipped"
    assert manager.ensure_once() == "skipped"
    disabled_logs = [r for r in caplog.records if "disabled" in r.message]
    assert len(disabled_logs) <= 1
    assert adapter.created == 0


def test_missing_subscription_is_created():
    adapter = FakeAdapter(subscriptions=[])
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "created"
    assert adapter.created == 1


def test_url_mismatch_counts_as_missing():
    adapter = FakeAdapter(subscriptions=[_subscription(url="https://old.example.com/hook")])
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "created"


def test_healthy_subscription_is_noop():
    adapter = FakeAdapter(subscriptions=[_subscription(hours_from_now=20.0)])
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "ok"
    assert adapter.created == 0
    assert adapter.renewed == []


def test_near_expiry_subscription_is_renewed():
    adapter = FakeAdapter(subscriptions=[_subscription(hours_from_now=2.0)])
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "renewed"
    assert adapter.renewed == ["sub-1"]


def test_unparseable_expiry_is_renewed():
    subscription = _subscription()
    subscription["expirationDateTime"] = "not-a-date"
    adapter = FakeAdapter(subscriptions=[subscription])
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "renewed"


def test_adapter_failure_returns_failed_without_raising():
    adapter = FakeAdapter(raise_on_list=True)
    manager = SubscriptionManager(lambda: adapter, _settings())
    assert manager.ensure_once() == "failed"


def test_parse_graph_datetime_handles_seven_digit_fraction():
    parsed = parse_graph_datetime("2026-07-15T15:00:00.0000000Z")
    assert parsed == datetime(2026, 7, 15, 15, 0, 0, tzinfo=UTC)


def test_parse_graph_datetime_rejects_garbage():
    assert parse_graph_datetime("") is None
    assert parse_graph_datetime(None) is None
    assert parse_graph_datetime("tomorrow-ish") is None