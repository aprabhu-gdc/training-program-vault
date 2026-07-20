"""WikiIntegrationError category detection for index-not-ready states."""

from __future__ import annotations

from teams_bot.services.wiki_query import WikiIntegrationError, _is_index_not_ready


class IndexNotReadyError(RuntimeError):
    """Stand-in matched by name, exactly as the bot layer detects it."""


def test_default_category_is_backend():
    assert WikiIntegrationError("boom").category == "backend"


def test_explicit_category_is_preserved():
    assert WikiIntegrationError("nope", category="index_not_ready").category == "index_not_ready"


def test_detects_index_not_ready_as_direct_cause():
    try:
        try:
            raise IndexNotReadyError("table missing")
        except IndexNotReadyError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as outer:
        assert _is_index_not_ready(outer)


def test_detects_index_not_ready_deep_in_context_chain():
    try:
        try:
            raise IndexNotReadyError("table missing")
        except IndexNotReadyError:
            raise ValueError("intermediate")  # implicit __context__ link
    except ValueError as outer:
        assert _is_index_not_ready(outer)


def test_unrelated_error_is_not_index_not_ready():
    assert not _is_index_not_ready(ValueError("something else"))
