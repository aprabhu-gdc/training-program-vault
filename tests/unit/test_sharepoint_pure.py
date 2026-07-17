"""Phase 09: pure (network-free) helpers on the SharePoint adapter."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from packages.contracts.sync import SourceFileEvent
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from tests.conftest import make_core_settings


@pytest.fixture
def adapter(tmp_path):
    # site_id + drive_id are pre-resolved in the factory, so _drive_id()/_site_id()
    # never hit Microsoft Graph. raw root = "raw/sources", wiki root = "wiki".
    settings = make_core_settings(tmp_path)
    return SharePointSourceSyncAdapter(settings)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("raw/sources", True),
        ("raw/sources/topic.docx", True),
        ("raw/sources/sub/dir/file.pdf", True),
        ("/raw/sources/leading-slash.docx", True),
        ("raw/other/file.docx", False),
        ("rawxsources/file.docx", False),
        ("wiki/concepts/x.md", False),
    ],
)
def test_is_in_scope(adapter, path, expected):
    assert adapter.is_in_scope(SourceFileEvent(path=path, event_type="webhook")) is expected


def test_graph_drive_item_url_quotes_path(adapter):
    url = adapter._graph_drive_item_url("raw/sources/a b.docx")
    assert url.endswith("/drives/drive-test-id/root:/raw/sources/a%20b.docx")
    assert url.startswith("https://graph.microsoft.com/v1.0")


def test_graph_item_content_url(adapter):
    assert adapter._graph_item_content_url("raw/sources/x.docx").endswith(
        "/root:/raw/sources/x.docx:/content"
    )


def test_graph_children_url_root_and_nested(adapter):
    assert adapter._graph_children_url("").endswith("/drives/drive-test-id/root/children")
    assert adapter._graph_children_url("raw/sources").endswith(
        "/root:/raw/sources:/children"
    )


def test_graph_parent_path_extracts_after_marker(adapter):
    item = {"parentReference": {"path": "/drives/abc/root:/raw/sources/sub"}}
    assert adapter._graph_parent_path(item) == "raw/sources/sub"


def test_graph_parent_path_no_marker_returns_empty(adapter):
    assert adapter._graph_parent_path({"parentReference": {"path": "/drives/abc/root"}}) == ""
    assert adapter._graph_parent_path({}) == ""


@pytest.mark.parametrize(
    "sharepoint_path,expected",
    [
        ("raw/sources/sub/file.docx", Path("sub/file.docx")),
        ("raw/sources", Path()),
        ("raw/sources/file.pdf", Path("file.pdf")),
    ],
)
def test_relative_raw_path(adapter, sharepoint_path, expected):
    assert adapter._relative_raw_path(sharepoint_path) == expected


@pytest.mark.parametrize(
    "relative,expected",
    [
        ("wiki/concepts/x.md", "wiki/concepts/x.md"),
        ("concepts/x.md", "wiki/concepts/x.md"),
        ("/wiki/sources/y.md", "wiki/sources/y.md"),
    ],
)
def test_remote_wiki_path(adapter, relative, expected):
    assert adapter._remote_wiki_path(relative) == expected


def test_format_expiration_shape(adapter):
    value = adapter._format_expiration(60)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.0000000Z", value)


def test_download_file_converts_legacy_doc_to_pdf(adapter):
    """Legacy .doc is fetched as PDF (Graph server-side conversion) and lands at
    a .pdf destination so a local PDF extractor can read it."""

    captured = {}

    def fake_download(remote_path, destination, *, convert_to_pdf=False):
        captured["remote_path"] = remote_path
        captured["destination"] = destination
        captured["convert_to_pdf"] = convert_to_pdf
        return destination

    adapter.download_remote_file = fake_download  # type: ignore[method-assign]
    result = adapter.download_file("raw/sources/Paint/Macropoxy 920.doc")

    assert captured["convert_to_pdf"] is True
    assert result.name == "Macropoxy 920.doc.pdf"
    assert result.suffix == ".pdf"


def test_download_file_leaves_supported_types_untouched(adapter):
    captured = {}

    def fake_download(remote_path, destination, *, convert_to_pdf=False):
        captured["convert_to_pdf"] = convert_to_pdf
        return destination

    adapter.download_remote_file = fake_download  # type: ignore[method-assign]
    result = adapter.download_file("raw/sources/topic.pdf")

    assert captured["convert_to_pdf"] is False
    assert result.name == "topic.pdf"


def test_content_url_format_pdf_suffix(adapter):
    base = adapter._graph_item_content_url("raw/sources/x.doc")
    assert (base + "?format=pdf").endswith("/root:/raw/sources/x.doc:/content?format=pdf")
