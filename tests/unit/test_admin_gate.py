"""Admin allowlist parsing in the bot Settings."""

from __future__ import annotations

from teams_bot.config import _parse_admin_object_ids


def test_empty_allowlist_is_empty_set():
    assert _parse_admin_object_ids("") == frozenset()
    assert _parse_admin_object_ids("   ") == frozenset()


def test_parses_commas_semicolons_and_whitespace():
    parsed = _parse_admin_object_ids("AAA, bbb;ccc\nddd  eee")
    assert parsed == {"aaa", "bbb", "ccc", "ddd", "eee"}


def test_object_ids_are_lowercased_for_case_insensitive_match():
    # Entra object IDs are case-insensitive GUIDs.
    assert _parse_admin_object_ids("00000000-ABCD-0000-0000-000000000000") == {
        "00000000-abcd-0000-0000-000000000000"
    }
