"""Bot-layer command dispatch and the admin gate (the security-critical surface)."""

from __future__ import annotations

from botbuilder.core import ConversationState, MemoryStorage, UserState
from botbuilder.schema import Activity, ChannelAccount, ConversationAccount

from teams_bot.bot import GraydazeTrainingBot
from teams_bot.commands import parse_command
from teams_bot.config import Settings


class StubTurnContext:
    def __init__(self, activity):
        self.activity = activity
        self.sent = []

    async def send_activity(self, activity):
        self.sent.append(activity)
        return type("Resp", (), {"id": "act-1"})()


def _bot(admin_ids=frozenset()):
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
        admin_object_ids=admin_ids,
    )
    storage = MemoryStorage()
    return GraydazeTrainingBot(
        settings=settings,
        user_state=UserState(storage),
        conversation_state=ConversationState(storage),
        wiki_query_service=object(),
        feedback_logger=object(),
        ingest_admin_client=object(),
        analytics=object(),
    )


def _activity(text: str, *, aad: str | None, name: str = "Pat PM"):
    return Activity(
        type="message",
        text=text,
        from_property=ChannelAccount(id="29:user", name=name, aad_object_id=aad),
        conversation=ConversationAccount(id="conv-1"),
        channel_id="msteams",
    )


def _text(context) -> str:
    return " ".join(str(a) for a in context.sent)


async def test_whoami_returns_object_id_to_anyone():
    bot = _bot(admin_ids=frozenset())
    ctx = StubTurnContext(_activity("/whoami", aad="abc-123"))
    await bot._dispatch_command(ctx, parse_command("/whoami"))
    assert "abc-123" in _text(ctx)


async def test_non_admin_sync_is_denied_and_does_not_run(monkeypatch):
    bot = _bot(admin_ids=frozenset({"admin-aad"}))
    ran = []
    monkeypatch.setattr(bot, "_handle_sync_command", lambda ctx: ran.append(True))
    ctx = StubTurnContext(_activity("/sync", aad="someone-else"))
    await bot._dispatch_command(ctx, parse_command("/sync"))
    assert not ran
    assert "admin-only" in _text(ctx)


async def test_empty_allowlist_disables_admin_commands(monkeypatch):
    bot = _bot(admin_ids=frozenset())
    ran = []
    monkeypatch.setattr(bot, "_handle_sync_command", lambda ctx: ran.append(True))
    ctx = StubTurnContext(_activity("/sync", aad="admin-aad"))
    await bot._dispatch_command(ctx, parse_command("/sync"))
    assert not ran
    assert "disabled" in _text(ctx)


async def test_admin_sync_reaches_handler(monkeypatch):
    bot = _bot(admin_ids=frozenset({"admin-aad"}))
    ran = []

    async def _fake_sync(ctx):
        ran.append(True)

    monkeypatch.setattr(bot, "_handle_sync_command", _fake_sync)
    ctx = StubTurnContext(_activity("/sync", aad="ADMIN-AAD"))  # case-insensitive match
    await bot._dispatch_command(ctx, parse_command("/sync"))
    assert ran == [True]


async def test_unknown_command_is_reported_not_answered():
    bot = _bot()
    ctx = StubTurnContext(_activity("/frobnicate", aad="admin-aad"))
    await bot._dispatch_command(ctx, parse_command("/frobnicate"))
    assert "Unknown command" in _text(ctx)


async def test_non_admin_remove_never_reaches_the_llm(monkeypatch):
    bot = _bot(admin_ids=frozenset({"admin-aad"}))
    reached = []
    monkeypatch.setattr(bot, "_handle_remove", lambda ctx, args: reached.append(args))
    ctx = StubTurnContext(_activity("/remove wiki/sources/foo.md", aad="not-admin"))
    await bot._dispatch_command(ctx, parse_command("/remove wiki/sources/foo.md"))
    assert not reached
    assert "admin-only" in _text(ctx)
