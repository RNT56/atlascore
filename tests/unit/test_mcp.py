"""Unit tests for the MCP server surface + `atlas mcp` CLI (offline; no live DB).

Building the server and listing its tools needs no database (the engine connects lazily on the
first tool call), so the tool contract and the hit→payload mapping are verified offline.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app
from atlascore.mcp_server import TOOL_SEARCH_DOCUMENTS, build_mcp_server
from atlascore.mcp_server.server import _hit_to_payload
from atlascore.storage.search import SearchHit

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_mcp_help_lists_dev() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "dev" in result.output


def test_build_server_registers_search_documents() -> None:
    server = build_mcp_server()  # no engine -> no DB touched while listing
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_SEARCH_DOCUMENTS)
    assert tool.inputSchema["required"] == ["query"]
    assert tool.inputSchema["properties"]["limit"]["maximum"] == 50
    assert tool.description is not None
    assert "untrusted evidence" in tool.description.lower()


def test_hit_to_payload_shape() -> None:
    hit = SearchHit(
        document_id=UUID(int=1),
        source_id=UUID(int=2),
        source_slug="slug",
        source_name="Name",
        canonical_url="https://x.test/a",
        title="T",
        language="en",
        topics=["macro"],
        entity_ids=["e1"],
        published_at=datetime(2026, 6, 4, tzinfo=UTC),
        ingested_at=datetime(2026, 6, 4, 1, tzinfo=UTC),
        parser_version="article@1.0.0",
        rank=0.5,
        snippet="«rates» cut",
    )
    payload = _hit_to_payload(hit)
    assert payload["document_id"] == str(UUID(int=1))
    assert payload["topics"] == ["macro"]
    assert payload["snippet"] == "«rates» cut"
    prov = payload["provenance"]
    assert prov["source_id"] == str(UUID(int=2))
    assert prov["source"] == "Name"
    assert prov["canonical_url"] == "https://x.test/a"
    assert prov["published_at"] == "2026-06-04T00:00:00+00:00"
    assert prov["parser_version"] == "article@1.0.0"
