"""Unit tests for the `get_raw_document` tool contract + payload helpers (offline; no DB).

The DB-backed sanitized-vs-raw + audit path is in the integration suite; here we pin the tool
schema (sanitized-by-default, byte caps, both id inputs) and the payload/truncation helpers.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from atlascore.mcp_server import (
    RAW_MAX_BYTES_DEFAULT,
    RAW_MAX_BYTES_MAX,
    TOOL_GET_RAW_DOCUMENT,
    build_mcp_server,
)
from atlascore.mcp_server.server import _raw_document_to_payload, _truncate_utf8
from atlascore.storage.raw_documents import RawDocumentContent

pytestmark = [pytest.mark.unit, pytest.mark.security]

_HTML = b'<p>Hello</p><script>steal()</script><a href="javascript:x()">y</a>'


def _content() -> RawDocumentContent:
    return RawDocumentContent(
        raw_document_id=UUID(int=1),
        document_id=UUID(int=2),
        raw_bytes=_HTML,
        mime_type="text/html",
        content_hash="sha256:abc",
        size_bytes=len(_HTML),
        http_status=200,
        etag='"v1"',
        last_modified="Mon, 01 Jun 2026 08:00:00 GMT",
        fetched_at=None,
        source_id=UUID(int=3),
        source_name="Some Feed",
        canonical_url="https://x.test/a",
    )


def test_tool_registered_sanitized_by_default() -> None:
    server = build_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_GET_RAW_DOCUMENT)
    props = tool.inputSchema["properties"]
    assert props["include_raw_html"]["default"] is False
    assert props["max_bytes"]["maximum"] == RAW_MAX_BYTES_MAX
    assert props["max_bytes"]["default"] == RAW_MAX_BYTES_DEFAULT
    assert {"document_id", "raw_document_id", "include_raw_html", "max_bytes"}.issubset(props)
    assert "injection-sensitive" in (tool.description or "").lower()


def test_payload_default_is_sanitized() -> None:
    payload = _raw_document_to_payload(_content(), include_raw_html=False, max_bytes=65536)
    assert payload["content_kind"] == "sanitized_text"
    assert "Hello" in payload["content"]
    for needle in ["steal", "<script", "javascript:"]:
        assert needle not in payload["content"]
    assert "untrusted_evidence_notice" not in payload  # only on the raw path
    assert payload["provenance"]["content_hash"] == "sha256:abc"


def test_payload_raw_html_is_wrapped() -> None:
    payload = _raw_document_to_payload(_content(), include_raw_html=True, max_bytes=65536)
    assert payload["content_kind"] == "raw_html"
    assert "<script>steal()</script>" in payload["content"]  # unsanitized, verbatim
    assert payload["untrusted_evidence_notice"].startswith("This is RAW")


def test_truncate_utf8() -> None:
    assert _truncate_utf8("hello", 100) == ("hello", False)
    text, truncated = _truncate_utf8("abcdef", 3)
    assert truncated is True and text == "abc"


def test_payload_truncates_over_cap() -> None:
    big = RawDocumentContent(
        raw_document_id=UUID(int=1),
        document_id=None,
        raw_bytes=b"x" * 5000,
        mime_type="text/plain",
        content_hash="sha256:big",
        size_bytes=5000,
        http_status=200,
        etag=None,
        last_modified=None,
        fetched_at=None,
        source_id=UUID(int=3),
        source_name="Big",
        canonical_url=None,
    )
    payload = _raw_document_to_payload(big, include_raw_html=True, max_bytes=1024)
    assert payload["truncated"] is True
    assert len(payload["content"].encode("utf-8")) <= 1024
    assert payload["size_bytes"] == 5000  # full size reported in provenance
