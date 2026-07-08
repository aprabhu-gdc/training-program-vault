"""Readability changes: inline source-tag stripping, the Sources+Feedback card,
and citation preservation through the Teams query adapter."""

from __future__ import annotations

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import Citation, QueryRequest, QueryResponse
from packages.wiki_core.content.markdown import strip_source_tags
from teams_bot.cards import build_answer_card
from teams_bot.markdown_card import markdown_to_adaptive_elements
from teams_bot.services.source_links import SourceLinkResolver
from teams_bot.services.wiki_query import WikiQueryService


def test_strip_source_tags_removes_inline_citations():
    text = "An ETC is the cost to finish. [Source: Cost Management] Update it monthly. [Sources: A, B]"
    assert strip_source_tags(text) == "An ETC is the cost to finish. Update it monthly."


def test_strip_source_tags_is_case_insensitive_and_collapses_spaces():
    assert strip_source_tags("Foo  [source: X]  bar") == "Foo bar"


def test_strip_source_tags_leaves_normal_brackets_untouched():
    assert strip_source_tags("Use array[0] and [see below].") == "Use array[0] and [see below]."


def _body(attachment):
    return attachment.content["body"]


def _by_id(body, element_id):
    return next(e for e in body if e.get("id") == element_id)


def test_answer_card_renders_source_links_collapsed_by_default():
    att = build_answer_card("req-1", "Lead paragraph.", sources=[{"title": "Cost Management", "url": "https://sp/x.md"}])
    sources = _by_id(_body(att), "sourcesSection")
    assert sources["isVisible"] is False
    assert sources["items"][0]["text"] == "[Cost Management](https://sp/x.md)"
    assert _by_id(_body(att), "feedbackSection")["isVisible"] is False


def test_answer_card_uses_plain_text_when_no_url():
    att = build_answer_card("req-1", "Lead paragraph.", sources=[{"title": "Cost Management", "url": None}])
    sources = _by_id(_body(att), "sourcesSection")
    assert sources["items"][0]["text"] == "Cost Management"


def test_answer_card_omits_sources_section_when_empty():
    att = build_answer_card("req-1", "Lead paragraph.", sources=[])
    assert all(e.get("id") != "sourcesSection" for e in _body(att))


def test_answer_card_preserves_feedback_payload():
    att = build_answer_card("req-42", "Lead paragraph.")
    feedback = _by_id(_body(att), "feedbackSection")
    action_set = feedback["items"][1]
    payloads = [a["data"] for a in action_set["actions"]]
    assert {"action": "feedback", "feedback": "helpful", "request_id": "req-42"} in payloads
    assert {"action": "feedback", "feedback": "inaccurate", "request_id": "req-42"} in payloads


async def test_wiki_query_service_preserves_citations_from_structured_result():
    async def fake_callable(**kwargs):
        return QueryResponse(
            answer_text="Grounded answer.",
            citations=(Citation(title="T", path="wiki/t.md", section="Overview", sources=("raw/x.docx",)),),
        )

    service = WikiQueryService(fake_callable, timeout_seconds=5)
    request = QueryRequest(
        request_id="r",
        query="q",
        identity=CallerIdentity(
            user_id=None, user_name=None, tenant_id=None, client_app="test",
            channel_id=None, conversation_id=None, locale=None,
        ),
    )

    result = await service.query(request)

    assert result.answer_text == "Grounded answer."
    assert len(result.citations) == 1
    assert result.citations[0].title == "T"
    assert result.citations[0].path == "wiki/t.md"


def _resolver_with_base(base):
    resolver = SourceLinkResolver()
    resolver._attempted = True  # skip the live drive lookup
    resolver._base_url = base
    return resolver


def test_source_link_appends_web_param_to_open_in_viewer():
    resolver = _resolver_with_base("https://host/Training%20Program%20Vault")
    assert (
        resolver.link_for("wiki/concepts/etc.md")
        == "https://host/Training%20Program%20Vault/wiki/concepts/etc.md?web=1"
    )
    # Leading/trailing slashes are normalized before the ?web=1 suffix.
    assert resolver.link_for("/wiki/x.md/") == "https://host/Training%20Program%20Vault/wiki/x.md?web=1"


def test_source_link_returns_none_when_base_unresolved():
    resolver = _resolver_with_base(None)
    assert resolver.link_for("wiki/x.md") is None


# --- Markdown -> Adaptive Card renderer ---


def test_markdown_headings_render_as_sized_bold_textblocks():
    els = markdown_to_adaptive_elements("## Overview\n\nBody text.\n\n### Detail\n\nMore.")
    headers = [e for e in els if e.get("weight") == "Bolder"]
    assert [h["text"] for h in headers] == ["Overview", "Detail"]
    assert headers[0]["size"] == "Medium" and headers[0]["separator"] is True
    assert headers[1]["size"] == "Default"
    bodies = [e["text"] for e in els if e.get("weight") != "Bolder"]
    assert "Body text." in bodies and "More." in bodies


def test_markdown_bullets_collapse_into_one_list_textblock():
    els = markdown_to_adaptive_elements("Steps:\n\n- First\n- Second\n- Third")
    list_blocks = [e for e in els if "\r" in e["text"]]
    assert len(list_blocks) == 1
    assert list_blocks[0]["text"] == "- First\r- Second\r- Third"


def test_markdown_numbered_list_preserves_numbers():
    els = markdown_to_adaptive_elements("1. One\n2. Two")
    assert any(e["text"] == "1. One\r2. Two" for e in els)


def test_markdown_empty_falls_back_to_single_block():
    els = markdown_to_adaptive_elements("")
    assert len(els) == 1 and els[0]["type"] == "TextBlock"


def test_answer_card_renders_answer_before_sections():
    att = build_answer_card("req-1", "## Overview\n\nThe body.", sources=[{"title": "S", "url": "https://x/y.md"}])
    body = _body(att)
    assert body[0]["text"] == "Overview" and body[0]["weight"] == "Bolder"
    assert _by_id(body, "sourcesSection")["isVisible"] is False
    assert _by_id(body, "feedbackSection")["isVisible"] is False
