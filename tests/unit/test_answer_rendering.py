"""Readability changes: inline source-tag stripping, the Sources+Feedback card,
and citation preservation through the Teams query adapter."""

from __future__ import annotations

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import Citation, QueryRequest, QueryResponse
from packages.wiki_core.content.markdown import strip_source_tags
from teams_bot.cards import build_answer_card
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
    att = build_answer_card("req-1", sources=[{"title": "Cost Management", "url": "https://sp/x.md"}])
    sources = _by_id(_body(att), "sourcesSection")
    assert sources["isVisible"] is False
    assert sources["items"][0]["text"] == "[Cost Management](https://sp/x.md)"
    assert _by_id(_body(att), "feedbackSection")["isVisible"] is False


def test_answer_card_uses_plain_text_when_no_url():
    att = build_answer_card("req-1", sources=[{"title": "Cost Management", "url": None}])
    sources = _by_id(_body(att), "sourcesSection")
    assert sources["items"][0]["text"] == "Cost Management"


def test_answer_card_omits_sources_section_when_empty():
    att = build_answer_card("req-1", sources=[])
    assert all(e.get("id") != "sourcesSection" for e in _body(att))


def test_answer_card_preserves_feedback_payload():
    att = build_answer_card("req-42")
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
