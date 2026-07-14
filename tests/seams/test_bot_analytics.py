"""Analytics seams: AnalyticsService payloads and the bot's feedback parsing.

The privacy contract is asserted structurally here: the exact field names sent
to SharePoint must match the provisioned columns, none of which can hold
question or answer text.
"""

from __future__ import annotations

import asyncio

from botbuilder.core import ConversationState, MemoryStorage, UserState
from botbuilder.schema import Activity, ChannelAccount, ConversationAccount

from packages.wiki_core.analytics.sharepoint_lists import (
    FEEDBACK_COLUMNS,
    QUERY_EVENT_COLUMNS,
)
from teams_bot.bot import GraydazeTrainingBot
from teams_bot.config import Settings
from teams_bot.services.analytics import AnalyticsService
from tests.conftest import make_core_settings


class FakeListClient:
    def __init__(self, raise_on_write: Exception | None = None):
        self.items: list[tuple[str, dict]] = []
        self._raise = raise_on_write

    def create_item(self, list_name, fields):
        if self._raise is not None:
            raise self._raise
        self.items.append((list_name, dict(fields)))


# --- AnalyticsService payloads ---


async def test_record_query_writes_one_row_per_concept_matching_list_columns():
    client = FakeListClient()
    service = AnalyticsService(client=client)
    await service.record_query(
        request_id="req-1",
        user_id="29:user",
        user_name="Pat PM",
        concepts=("Estimate to Complete", "Mission Support"),
    )

    assert [name for name, _ in client.items] == ["TrainingBotQueryEvents"] * 2
    allowed = {"Title"} | {column["name"] for column in QUERY_EVENT_COLUMNS}
    for _, fields in client.items:
        assert set(fields) == allowed
        assert fields["IsUnknown"] is False
    assert client.items[0][1]["Concept"] == "Estimate to Complete"
    assert client.items[1][1]["Concept"] == "Mission Support"


async def test_record_query_marks_unknown():
    client = FakeListClient()
    service = AnalyticsService(client=client)
    await service.record_query(
        request_id="req-2", user_id=None, user_name=None, concepts=("Unknown",)
    )
    _, fields = client.items[0]
    assert fields["Concept"] == "Unknown"
    assert fields["IsUnknown"] is True
    assert fields["UserId"] == "" and fields["UserName"] == ""


async def test_record_feedback_matches_list_columns():
    client = FakeListClient()
    service = AnalyticsService(client=client)
    await service.record_feedback(
        request_id="req-3",
        user_id="29:user",
        user_name="Pat PM",
        rating="inaccurate",
        comment="The ETC formula was outdated.",
        concepts=("Estimate to Complete",),
    )

    list_name, fields = client.items[0]
    assert list_name == "TrainingBotFeedback"
    assert set(fields) == {"Title"} | {column["name"] for column in FEEDBACK_COLUMNS}
    assert fields["Rating"] == "inaccurate"
    assert fields["Comment"] == "The ETC formula was outdated."
    assert fields["Concepts"] == "Estimate to Complete"


async def test_record_methods_swallow_write_failures():
    service = AnalyticsService(client=FakeListClient(raise_on_write=RuntimeError("boom")))
    await service.record_query(request_id="r", user_id=None, user_name=None, concepts=("C",))
    await service.record_feedback(
        request_id="r", user_id=None, user_name=None, rating="helpful", comment="", concepts=()
    )


async def test_disabled_via_settings_records_nothing(tmp_path):
    settings = make_core_settings(tmp_path, analytics_enabled=False)
    service = AnalyticsService(settings=settings)
    await service.record_query(request_id="r", user_id=None, user_name=None, concepts=("C",))
    assert service._client is None


async def test_init_failure_disables_for_process_lifetime(tmp_path):
    # Missing SharePoint auth makes SharePointListClient construction raise.
    settings = make_core_settings(tmp_path, sharepoint_tenant_id="")
    service = AnalyticsService(settings=settings)
    await service.record_query(request_id="r", user_id=None, user_name=None, concepts=("C",))
    assert service._attempted is True and service._client is None


# --- Bot feedback parsing ---


class FakeAnalytics:
    def __init__(self):
        self.feedback = []

    async def record_query(self, **kwargs):  # pragma: no cover - not used here
        pass

    async def record_feedback(self, **kwargs):
        self.feedback.append(kwargs)


class FakeFeedbackLogger:
    def __init__(self):
        self.events = []

    async def log(self, event):
        self.events.append(event)


class StubTurnContext:
    def __init__(self, activity):
        self.activity = activity
        self.sent = []

    async def send_activity(self, activity):
        self.sent.append(activity)


def _bot(analytics, feedback_logger):
    settings = Settings(
        app_id="",
        app_password="",
        app_type="MultiTenant",
        app_tenant_id="",
        port=3978,
        wiki_query_callable="x:y",
        wiki_query_http_url="",
        ingest_admin_http_url="http://localhost:8010",
        wiki_query_timeout_seconds=5.0,
        welcome_examples=("a", "b"),
    )
    storage = MemoryStorage()
    return GraydazeTrainingBot(
        settings=settings,
        user_state=UserState(storage),
        conversation_state=ConversationState(storage),
        wiki_query_service=object(),
        feedback_logger=feedback_logger,
        ingest_admin_client=object(),
        analytics=analytics,
    )


def _feedback_activity(value):
    return Activity(
        type="message",
        value=value,
        from_property=ChannelAccount(id="29:user", name="Pat PM"),
        conversation=ConversationAccount(id="conv-1"),
        channel_id="msteams",
        channel_data={"tenant": {"id": "tid"}},
    )


async def _drain(bot):
    if bot._analytics_tasks:
        await asyncio.gather(*bot._analytics_tasks)


async def test_feedback_with_comment_and_concepts_reaches_analytics():
    analytics, logger = FakeAnalytics(), FakeFeedbackLogger()
    bot = _bot(analytics, logger)
    context = StubTurnContext(
        _feedback_activity(
            {
                "action": "feedback",
                "feedback": "inaccurate",
                "request_id": "req-9",
                "concepts": ["Estimate to Complete"],
                "comment": "  Outdated formula.  ",
            }
        )
    )

    await bot._handle_feedback(context)
    await _drain(bot)

    event = logger.events[0]
    assert event.comment == "Outdated formula."
    assert event.concepts == ("Estimate to Complete",)
    recorded = analytics.feedback[0]
    assert recorded["rating"] == "inaccurate"
    assert recorded["comment"] == "Outdated formula."
    assert recorded["concepts"] == ("Estimate to Complete",)
    assert context.sent  # user still gets the acknowledgement


async def test_feedback_from_pre_analytics_cards_defaults_safely():
    analytics, logger = FakeAnalytics(), FakeFeedbackLogger()
    bot = _bot(analytics, logger)
    context = StubTurnContext(
        _feedback_activity({"action": "feedback", "feedback": "helpful", "request_id": "req-1"})
    )

    await bot._handle_feedback(context)
    await _drain(bot)

    event = logger.events[0]
    assert event.comment == "" and event.concepts == ()
    assert analytics.feedback[0]["comment"] == ""


async def test_feedback_comment_is_truncated():
    analytics, logger = FakeAnalytics(), FakeFeedbackLogger()
    bot = _bot(analytics, logger)
    context = StubTurnContext(
        _feedback_activity(
            {"action": "feedback", "feedback": "helpful", "request_id": "r", "comment": "x" * 5000}
        )
    )

    await bot._handle_feedback(context)
    await _drain(bot)
    assert len(logger.events[0].comment) == 1000


async def test_failing_analytics_never_breaks_the_acknowledgement():
    class ExplodingAnalytics(FakeAnalytics):
        async def record_feedback(self, **kwargs):
            raise RuntimeError("sink down")

    logger = FakeFeedbackLogger()
    bot = _bot(ExplodingAnalytics(), logger)
    context = StubTurnContext(
        _feedback_activity({"action": "feedback", "feedback": "helpful", "request_id": "r"})
    )

    await bot._handle_feedback(context)
    if bot._analytics_tasks:
        await asyncio.gather(*bot._analytics_tasks, return_exceptions=True)
    assert context.sent
