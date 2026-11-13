"""Pure-logic tests for the Claude analytics connector (no MCP runtime needed)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.analytics_mcp.concepts import ConceptInputError, resolve_concept
from apps.analytics_mcp.config import ConnectorSettings
from apps.analytics_mcp.identity import NotSignedInError, caller_from_claims
from apps.analytics_mcp.report import ReportFilter, build_report


# --- concepts --------------------------------------------------------------


def test_no_page_is_unknown():
    ref = resolve_concept(None, None)
    assert ref.is_unknown and ref.label == "Unknown"
    assert resolve_concept("", "Unknown").is_unknown


def test_concept_path_uses_curated_label():
    ref = resolve_concept("wiki/concepts/estimate-to-complete.md", "Estimate to Complete")
    assert ref.path == "wiki/concepts/estimate-to-complete.md"
    assert ref.label == "Estimate to Complete (ETC)"
    assert ref.title == "Estimate to Complete"


@pytest.mark.parametrize(
    "raw",
    [
        "https://graydazeb.sharepoint.com/Training Program Vault/wiki/concepts/retainage.md",
        "https://graydazeb.sharepoint.com/Training%20Program%20Vault/wiki/concepts/retainage.md?web=1",
        "wiki/wiki/concepts/retainage.md",
        "wiki\\concepts\\retainage.md",
    ],
)
def test_path_variants_normalize(raw):
    assert resolve_concept(raw, "Retainage").path == "wiki/concepts/retainage.md"


def test_title_derived_from_path_when_missing():
    ref = resolve_concept("wiki/concepts/slab-stabilization.md", None)
    assert ref.title == "Slab Stabilization"


@pytest.mark.parametrize(
    "path,title",
    [
        ("raw/sources/contract.pdf", "Contract"),
        ("wiki/concepts/../../.env", "x"),
        ("not a path", "Retainage"),
        (None, "Retainage"),  # title without a path
    ],
)
def test_bad_paths_rejected(path, title):
    with pytest.raises(ConceptInputError):
        resolve_concept(path, title)


@pytest.mark.parametrize(
    "title",
    [
        "How deep should we fill a slab joint?",
        "what is the minimum depth for joint filler when the saw cut is deeper than two inches on a slab",
        'Notes on "fill depth" from the field',
        "x" * 101,
    ],
)
def test_question_like_titles_rejected(title):
    # Privacy: the Title column must never carry question text.
    with pytest.raises(ConceptInputError):
        resolve_concept("wiki/concepts/aci-joint-filler-depth.md", title)


# --- identity --------------------------------------------------------------


def test_caller_from_entra_claims():
    caller = caller_from_claims(
        {"oid": "abc", "name": "Pat PM", "tid": "t1", "roles": ["Analytics.Admin"]}, tenant_id="t1"
    )
    assert caller.user_id == "abc" and caller.user_name == "Pat PM"
    assert caller.has_role("Analytics.Admin")


def test_caller_requires_user_and_tenant():
    with pytest.raises(NotSignedInError):
        caller_from_claims(None)
    with pytest.raises(NotSignedInError):
        caller_from_claims({"name": "app-only"})
    with pytest.raises(NotSignedInError):
        caller_from_claims({"oid": "abc", "tid": "other"}, tenant_id="t1")


def test_caller_name_falls_back():
    assert caller_from_claims({"oid": "abc", "preferred_username": "pat@x.com"}).user_name == "pat@x.com"
    assert caller_from_claims({"oid": "abc"}).user_name == "abc"


# --- config ----------------------------------------------------------------


def _settings(**overrides):
    base = dict(
        base_url="https://connector.example.com",
        tenant_id="t",
        client_id="c",
        client_secret="s",
        jwt_signing_key="k" * 40,
        scope="analytics.use",
        admin_role="Analytics.Admin",
        allowed_redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
        state_connection_string="UseDevelopmentStorage=true",
        state_table="t",
        query_list_id="",
        feedback_list_id="",
        report_cache_seconds=60.0,
        record_limit_per_minute=30,
        dev_no_auth=False,
    )
    base.update(overrides)
    return ConnectorSettings(**base)


def test_valid_production_settings():
    assert _settings().validate() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"dev_no_auth": True},  # never unauthenticated on a public URL
        {"base_url": "http://connector.example.com"},
        {"jwt_signing_key": "short"},
        {"client_secret": ""},
        {"state_connection_string": ""},  # sessions would die on scale-to-zero
    ],
)
def test_unsafe_settings_rejected(overrides):
    with pytest.raises(ValueError):
        _settings(**overrides).validate()


def test_dev_no_auth_allowed_on_localhost():
    assert _settings(base_url="http://localhost:8000", dev_no_auth=True).validate()


# --- report ----------------------------------------------------------------


QUERY_ROWS = [
    {"RequestId": "r1", "UserId": "u1", "UserName": "Ana", "Concept": "Retainage", "IsUnknown": False,
     "Timestamp": "2026-11-01T15:00:00Z"},
    {"RequestId": "r2", "UserId": "u1", "UserName": "Ana", "Concept": "Retainage", "IsUnknown": False,
     "Timestamp": "2026-11-02T15:00:00Z"},
    {"RequestId": "r3", "UserId": "u2", "UserName": "Ben", "Concept": "Unknown", "IsUnknown": True,
     "Timestamp": "2026-11-02T16:00:00Z"},
    {"RequestId": "r4", "UserId": "u2", "UserName": "Ben", "Concept": "Coatings", "IsUnknown": False,
     "Timestamp": "2026-11-05T16:00:00Z"},
]
FEEDBACK_ROWS = [
    {"RequestId": "r1", "UserId": "u1", "UserName": "Ana", "Rating": "helpful", "Comment": "great",
     "Concepts": "Retainage", "Timestamp": "2026-11-01T15:05:00Z"},
    {"RequestId": "r4", "UserId": "u2", "UserName": "Ben", "Rating": "inaccurate", "Comment": "",
     "Concepts": "Coatings", "Timestamp": "2026-11-05T16:05:00Z"},
]


def test_report_matches_power_bi_measures():
    report = build_report(QUERY_ROWS, FEEDBACK_ROWS, ReportFilter())
    totals = report["totals"]
    assert totals["questions_asked"] == 4
    assert totals["unknown_questions"] == 1
    assert totals["feedback_count"] == 2
    assert totals["helpful_count"] == 1 and totals["inaccurate_count"] == 1
    assert totals["helpful_pct"] == 0.5
    # Unknown never appears on the concept chart.
    assert report["concepts"] == [
        {"concept": "Retainage", "questions": 2},
        {"concept": "Coatings", "questions": 1},
    ]
    assert report["feedback"][0]["user_name"] == "Ben"  # newest first


def test_report_date_and_user_filters():
    report = build_report(
        QUERY_ROWS, FEEDBACK_ROWS, ReportFilter(date(2026, 11, 2), date(2026, 11, 2), "u2")
    )
    assert report["totals"]["questions_asked"] == 1
    assert report["totals"]["unknown_questions"] == 1
    assert report["concepts"] == []
    assert report["totals"]["helpful_pct"] is None
    # The user slicer still lists everyone active in the date range.
    assert {u["user_id"] for u in report["users"]} == {"u1", "u2"}
    assert report["daily"] == [{"date": "2026-11-02", "questions": 1, "unknown": 1}]


# --- SharePoint list reads ---------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _list_client(pages):
    from packages.wiki_core.analytics.sharepoint_lists import SharePointListClient

    client = SharePointListClient.__new__(SharePointListClient)
    client._resolved_site_id = "site"
    client._list_ids = {}
    client.set_list_id("Events", "list-1")
    calls = []

    def fake_request(method, url, **_):
        calls.append(url)
        return _FakeResponse(pages[len(calls) - 1])

    client._request = fake_request
    return client, calls


def test_list_items_follows_paging_and_selects_fields():
    client, calls = _list_client(
        [
            {"value": [{"fields": {"Concept": "A", "Extra": "x"}}], "@odata.nextLink": "https://next"},
            {"value": [{"fields": {"Concept": "B"}}]},
        ]
    )
    rows, truncated = client.list_items("Events", ("Concept",))
    assert rows == [{"Concept": "A"}, {"Concept": "B"}]
    assert truncated is False
    assert "/lists/list-1/items" in calls[0] and calls[1] == "https://next"


def test_list_items_caps_rows():
    client, _ = _list_client([{"value": [{"fields": {"Concept": str(i)}} for i in range(5)]}])
    rows, truncated = client.list_items("Events", ("Concept",), max_items=3)
    assert len(rows) == 3 and truncated is True
