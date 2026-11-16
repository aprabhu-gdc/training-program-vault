"""Tool-level tests for the Claude analytics connector, run in-memory.

Skipped when fastmcp is not installed (it is a connector-only dependency:
apps/analytics_mcp/requirements.txt).
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from apps.analytics_mcp.identity import Caller  # noqa: E402
from apps.analytics_mcp.server import build_server  # noqa: E402


class FakeSink:
    def __init__(self):
        self.queries = []
        self.feedback = []

    async def record_query(self, **kwargs):
        self.queries.append(kwargs)

    async def record_feedback(self, **kwargs):
        self.feedback.append(kwargs)


ANA = Caller("u-ana", "Ana PM", frozenset())
BEN = Caller("u-ben", "Ben PM", frozenset())
ADMIN = Caller("u-admin", "Admin", frozenset({"Analytics.Admin"}))


async def _empty_source():
    return [], [], False


def _server(sink, who, **kwargs):
    state = {"who": who}
    server = build_server(sink=sink, source=_empty_source, caller_resolver=lambda: state["who"], **kwargs)
    return server, state


async def test_record_question_writes_concept_and_identity_only():
    sink = FakeSink()
    server, _ = _server(sink, ANA)
    async with Client(server) as client:
        result = await client.call_tool(
            "record_question",
            {"page_path": "wiki/concepts/estimate-to-complete.md", "page_title": "Estimate to Complete"},
        )
    data = result.structured_content
    assert len(data["request_id"]) == 32
    assert data["concept"] == "Estimate to Complete (ETC)"
    (row,) = sink.queries
    assert row == {
        "request_id": data["request_id"],
        "user_id": "u-ana",
        "user_name": "Ana PM",
        "concept": "Estimate to Complete (ETC)",
        "concept_title": "Estimate to Complete",
    }


async def test_record_unknown_question():
    sink = FakeSink()
    server, _ = _server(sink, ANA)
    async with Client(server) as client:
        result = await client.call_tool("record_question", {})
    assert result.structured_content["concept"] == "Unknown"
    assert sink.queries[0]["concept"] == "Unknown"


async def test_question_text_is_rejected_and_not_written():
    sink = FakeSink()
    server, _ = _server(sink, ANA)
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "record_question",
                {"page_path": "wiki/concepts/retainage.md", "page_title": "How much retainage do we hold?"},
            )
    assert sink.queries == []


async def test_no_tool_accepts_question_or_answer_text():
    server, _ = _server(FakeSink(), ANA)
    async with Client(server) as client:
        tools = await client.list_tools()
    params = {name for tool in tools for name in (tool.input_schema.get("properties") or {})}
    assert not params & {"question", "answer", "query", "text", "prompt", "user_name", "user_id_override"}


async def test_feedback_links_to_question_and_blocks_other_users():
    sink = FakeSink()
    server, state = _server(sink, ANA)
    async with Client(server) as client:
        asked = await client.call_tool("record_question", {"page_path": "wiki/concepts/coatings.md"})
        request_id = asked.structured_content["request_id"]
        await client.call_tool(
            "submit_feedback", {"request_id": request_id, "rating": "helpful", "comment": "clear"}
        )
        state["who"] = BEN
        with pytest.raises(ToolError):
            await client.call_tool("submit_feedback", {"request_id": request_id, "rating": "inaccurate"})
    (row,) = sink.feedback
    assert row["user_id"] == "u-ana" and row["rating"] == "helpful"
    assert row["concepts"] == ["Coatings"] and row["comment"] == "clear"


async def test_feedback_rejects_bad_request_id_and_rating():
    server, _ = _server(FakeSink(), ANA)
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("submit_feedback", {"request_id": "not-an-id", "rating": "helpful"})
        with pytest.raises(ToolError):
            await client.call_tool("submit_feedback", {"request_id": "a" * 32, "rating": "meh"})


async def test_rate_limit():
    sink = FakeSink()
    server, _ = _server(sink, ANA, record_limit_per_minute=2)
    async with Client(server) as client:
        await client.call_tool("record_question", {})
        await client.call_tool("record_question", {})
        with pytest.raises(ToolError):
            await client.call_tool("record_question", {})
    assert len(sink.queries) == 2


async def test_analytics_report_is_admin_only():
    server, state = _server(FakeSink(), ANA)
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_analytics", {})
        state["who"] = ADMIN
        result = await client.call_tool("get_analytics", {"start_date": "2026-11-01"})
    assert result.structured_content["totals"]["questions_asked"] == 0
    assert result.structured_content["filter"]["start_date"] == "2026-11-01"


def test_tool_annotations_mark_report_read_only():
    import asyncio

    async def annotations():
        server, _ = _server(FakeSink(), ANA)
        async with Client(server) as client:
            return {t.name: t.annotations for t in await client.list_tools()}

    found = asyncio.run(annotations())
    assert found["get_analytics"].read_only_hint is True
    assert found["record_question"].read_only_hint is False
    assert inspect.isfunction(build_server)
