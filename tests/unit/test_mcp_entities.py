"""Unit tests for the `get_entity_mentions` MCP tool contract + payload mappers (offline).

Listing tools and mapping rows needs no DB (the engine connects lazily on first call), so the
input schema, caps, and the entity/mention → JSON shape are verified offline.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from atlascore.mcp_server import TOOL_GET_ENTITY_MENTIONS, build_mcp_server
from atlascore.mcp_server.server import _entity_to_payload, _mention_to_payload
from atlascore.storage.entities import MentionRow, ResolvedEntity

pytestmark = pytest.mark.unit


def test_get_entity_mentions_registered_with_caps() -> None:
    server = build_mcp_server()  # no engine -> no DB touched while listing
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_GET_ENTITY_MENTIONS)
    props = tool.inputSchema["properties"]
    assert props["limit"]["maximum"] == 100
    assert props["min_confidence"]["maximum"] == 1.0
    assert {"entity_id", "name", "identifier"}.issubset(props.keys())
    assert tool.description is not None
    assert "untrusted evidence" in tool.description.lower()


def test_search_documents_has_topic_entity_filters() -> None:
    server = build_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == "search_documents")
    assert "topics" in tool.inputSchema["properties"]
    assert "entity_ids" in tool.inputSchema["properties"]


def test_entity_payload_shape() -> None:
    resolved = ResolvedEntity(
        entity_id=UUID(int=7),
        entity_type="company",
        canonical_name="Apple Inc",
        aliases=["apple inc", "aapl"],
        identifiers={"ticker": "AAPL"},
        merged_into=None,
        match_confidence=0.97,
    )
    payload = _entity_to_payload(resolved)
    assert payload["entity_id"] == str(UUID(int=7))
    assert payload["identifiers"] == {"ticker": "AAPL"}
    assert payload["merged_into"] is None
    assert payload["match_confidence"] == 0.97


def test_mention_payload_shape() -> None:
    row = MentionRow(
        mention_id=UUID(int=1),
        document_id=UUID(int=2),
        mentioned_at=datetime(2026, 6, 1, tzinfo=UTC),
        confidence=0.9,
        sentiment=None,
        context_snippet="…Apple Inc reported…",
        model_name="rule-ner",
        model_version="1.0.0",
        source_name="SEC EDGAR",
        source_id=UUID(int=3),
        canonical_url="https://x.test/a",
        published_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    payload = _mention_to_payload(row)
    assert payload["mention_id"] == str(UUID(int=1))
    assert payload["sentiment"] is None  # never fabricated
    assert payload["provenance"]["model_name"] == "rule-ner"
    assert payload["provenance"]["source"] == "SEC EDGAR"
