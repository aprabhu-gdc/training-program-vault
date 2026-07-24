"""Image attachment handling + short-term conversation memory in the Teams bot.

Covers the two fixes:
- images: authenticated Bot Connector downloads, HEIC/non-web transcoding, and
  large-image downscaling before they reach the vision model;
- memory: per-conversation history with turn/char caps and idle-based session
  reset, persisted through the already-wired ConversationState.
"""

from __future__ import annotations

import io

import pytest
from botbuilder.core import ConversationState, MemoryStorage, UserState
from botbuilder.schema import Activity, ChannelAccount, ConversationAccount
from PIL import Image

import teams_bot.bot as bot_module
from teams_bot.bot import GraydazeTrainingBot
from teams_bot.config import Settings


# --- helpers ---------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = dict(
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
    base.update(overrides)
    return Settings(**base)


def _bot(settings: Settings | None = None, storage: MemoryStorage | None = None) -> GraydazeTrainingBot:
    settings = settings or _settings()
    storage = storage or MemoryStorage()
    return GraydazeTrainingBot(
        settings=settings,
        user_state=UserState(storage),
        conversation_state=ConversationState(storage),
        wiki_query_service=object(),
        feedback_logger=object(),
        ingest_admin_client=object(),
    )


class FakeTurnContext:
    """Minimal context that satisfies Bot Framework state get/set/save."""

    def __init__(self, conversation_id: str = "conv-1", channel_id: str = "msteams"):
        self.activity = Activity(
            type="message",
            from_property=ChannelAccount(id="29:user", name="Pat"),
            conversation=ConversationAccount(id=conversation_id),
            channel_id=channel_id,
        )
        self.turn_state: dict = {}


def _png_bytes(size=(32, 32), color=(0, 128, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _bmp_bytes(size=(32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="BMP")
    return buffer.getvalue()


def _heic_bytes(size=(32, 32)) -> bytes | None:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        buffer = io.BytesIO()
        Image.new("RGB", size, (200, 50, 50)).save(buffer, format="HEIF")
        return buffer.getvalue()
    except Exception:
        return None


# --- image detection / preparation -----------------------------------------


def test_is_image_attachment_recognizes_heic_by_extension():
    assert GraydazeTrainingBot._is_image_attachment(name="photo.heic", content_type="")
    assert GraydazeTrainingBot._is_image_attachment(name="photo.HEIF", content_type="")
    assert GraydazeTrainingBot._is_image_attachment(name="x.png", content_type="")
    assert not GraydazeTrainingBot._is_image_attachment(name="notes.txt", content_type="text/plain")


def test_prepare_image_passes_png_through():
    bot = _bot()
    payload = _png_bytes()
    result = bot._prepare_image(payload=payload, name="x.png", content_type="image/png")
    assert result is not None
    data, content_type = result
    assert content_type == "image/png"
    assert data == payload  # untouched


def test_prepare_image_transcodes_bmp_to_jpeg():
    bot = _bot()
    result = bot._prepare_image(payload=_bmp_bytes(), name="x.bmp", content_type="image/bmp")
    assert result is not None
    data, content_type = result
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"


def test_prepare_image_transcodes_heic_to_jpeg():
    payload = _heic_bytes()
    if payload is None:
        pytest.skip("pillow-heif HEIC encoder unavailable in this environment")
    bot = _bot()
    result = bot._prepare_image(payload=payload, name="phone.heic", content_type="image/heic")
    assert result is not None
    data, content_type = result
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"


def test_prepare_image_downscales_oversized():
    bot = _bot()
    payload = _png_bytes(size=(3000, 1500))
    result = bot._prepare_image(payload=payload, name="big.png", content_type="image/png")
    assert result is not None
    data, _content_type = result
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) <= bot_module._MAX_IMAGE_EDGE


# --- authenticated attachment download --------------------------------------


def test_requires_bot_token_only_for_connector_hosts():
    connector = "https://smba.trafficmanager.net/amer/v3/attachments/1/views/original"
    botfx = "https://xyz.botframework.com/v3/attachments/1"
    sharepoint = "https://graydaze.sharepoint.com/sites/x/_layouts/download.aspx?id=1"
    assert GraydazeTrainingBot._requires_bot_token(connector)
    assert GraydazeTrainingBot._requires_bot_token(botfx)
    assert not GraydazeTrainingBot._requires_bot_token(sharepoint)


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def read(self):
        return self._data


class _FakeSession:
    last_headers: dict | None = None
    last_url: str | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None):
        _FakeSession.last_url = url
        _FakeSession.last_headers = dict(headers or {})
        return _FakeResponse(b"IMG-BYTES")


class _FakeCreds:
    def get_access_token(self, *args, **kwargs):
        return "tok-123"


async def test_download_attaches_bearer_for_connector_host(monkeypatch):
    monkeypatch.setattr(bot_module.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(
        bot_module.MicrosoftAppCredentials, "trust_service_url", staticmethod(lambda url: None)
    )
    bot = _bot()
    bot._app_credentials = _FakeCreds()

    data = await bot._download_attachment(
        "https://smba.trafficmanager.net/amer/v3/attachments/1/views/original"
    )
    assert data == b"IMG-BYTES"
    assert _FakeSession.last_headers.get("Authorization") == "Bearer tok-123"


async def test_download_stays_anonymous_for_sharepoint(monkeypatch):
    monkeypatch.setattr(bot_module.aiohttp, "ClientSession", _FakeSession)
    bot = _bot()
    bot._app_credentials = _FakeCreds()

    await bot._download_attachment("https://graydaze.sharepoint.com/x/download.aspx?id=1")
    assert "Authorization" not in _FakeSession.last_headers


# --- conversation memory ----------------------------------------------------


def test_trim_history_caps_turns_then_chars():
    bot = _bot(_settings(history_max_turns=4, history_char_budget=1000))
    turns = [{"role": "user", "text": f"m{i}"} for i in range(10)]
    trimmed = bot._trim_history(turns)
    assert trimmed == turns[-4:]  # newest 4 kept

    bot2 = _bot(_settings(history_max_turns=100, history_char_budget=10))
    long_turns = [{"role": "user", "text": "x" * 8} for _ in range(5)]
    trimmed2 = bot2._trim_history(long_turns)
    # Budget 10 keeps the most recent turn (always) plus nothing that overflows.
    assert 1 <= len(trimmed2) < 5


async def test_history_round_trip_persists_across_turns():
    storage = MemoryStorage()
    bot = _bot(_settings(history_max_turns=10, session_idle_minutes=30), storage=storage)

    ctx1 = FakeTurnContext()
    await bot._append_session_history(ctx1, user_text="What is ETC?", assistant_text="Estimate to complete.")
    await bot._conversation_state.save_changes(ctx1)

    ctx2 = FakeTurnContext()  # a fresh turn, same conversation
    history = await bot._load_session_history(ctx2)
    assert [(t.role, t.text) for t in history] == [
        ("user", "What is ETC?"),
        ("assistant", "Estimate to complete."),
    ]


async def test_idle_gap_resets_the_session(monkeypatch):
    storage = MemoryStorage()
    bot = _bot(_settings(session_idle_minutes=30), storage=storage)

    monkeypatch.setattr(bot_module.time, "time", lambda: 1_000.0)
    ctx1 = FakeTurnContext()
    await bot._append_session_history(ctx1, user_text="q1", assistant_text="a1")
    await bot._conversation_state.save_changes(ctx1)

    # Jump forward past the idle window -> the stale session is cleared.
    monkeypatch.setattr(bot_module.time, "time", lambda: 1_000.0 + 31 * 60)
    ctx2 = FakeTurnContext()
    assert await bot._load_session_history(ctx2) == []


async def test_recent_follow_up_keeps_history(monkeypatch):
    storage = MemoryStorage()
    bot = _bot(_settings(session_idle_minutes=30), storage=storage)

    monkeypatch.setattr(bot_module.time, "time", lambda: 5_000.0)
    ctx1 = FakeTurnContext()
    await bot._append_session_history(ctx1, user_text="q1", assistant_text="a1")
    await bot._conversation_state.save_changes(ctx1)

    monkeypatch.setattr(bot_module.time, "time", lambda: 5_000.0 + 60)  # 1 min later
    ctx2 = FakeTurnContext()
    history = await bot._load_session_history(ctx2)
    assert [t.text for t in history] == ["q1", "a1"]
