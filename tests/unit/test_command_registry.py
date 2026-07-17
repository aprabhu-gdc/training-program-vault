"""Slash-command parsing and the command registry."""

from __future__ import annotations

from teams_bot.commands import COMMANDS, parse_command


def test_plain_text_is_not_a_command():
    assert parse_command("what is an ETC?") is None
    assert parse_command("") is None


def test_slash_then_non_letter_is_not_a_command():
    # "/123" or "/ foo" must fall through to the wiki query path.
    assert parse_command("/123") is None
    assert parse_command("/ hello") is None


def test_known_command_without_args():
    parsed = parse_command("/sync")
    assert parsed is not None and parsed.spec is COMMANDS["sync"]
    assert parsed.args == ""


def test_command_is_case_insensitive_and_strips_arg_wrappers():
    parsed = parse_command("/REMOVE `wiki/sources/foo.md`")
    assert parsed is not None and parsed.spec is COMMANDS["remove"]
    assert parsed.args == "wiki/sources/foo.md"


def test_leading_and_trailing_whitespace_is_tolerated():
    parsed = parse_command("   /help   ")
    assert parsed is not None and parsed.spec is COMMANDS["help"]


def test_unknown_command_returns_spec_none_with_raw_name():
    parsed = parse_command("/frobnicate now")
    assert parsed is not None
    assert parsed.spec is None
    assert parsed.raw_name == "frobnicate"


def test_stopsync_does_not_collide_with_sync():
    assert parse_command("/stopsync").spec is COMMANDS["stopsync"]
    assert parse_command("/sync").spec is COMMANDS["sync"]


def test_admin_flags_are_set_as_expected():
    assert COMMANDS["whoami"].admin_only is False
    assert COMMANDS["help"].admin_only is False
    for name in ("sync", "stopsync", "remove", "clean", "lint"):
        assert COMMANDS[name].admin_only is True
