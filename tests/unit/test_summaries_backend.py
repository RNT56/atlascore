"""Unit tests for the summary backend + cache-key hashing (offline; no DB).

Pins the deterministic extractive summarizer (lead-sentence extraction + per-doc citations,
determinism), the backend factory's `LLM_BACKEND` branching, the `source_hash` over ordered member
content hashes, and the `get_document_summary` tool contract + payload mapper.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from atlascore.core.settings import Settings
from atlascore.enrichment.summaries import (
    ExtractiveSummarizer,
    SummaryInput,
    get_summary_backend,
)
from atlascore.enrichment.summaries.pipeline import SummaryOutcome
from atlascore.mcp_server import SUMMARY_DOCS_MAX, TOOL_GET_DOCUMENT_SUMMARY, build_mcp_server
from atlascore.mcp_server.server import _summary_to_payload
from atlascore.storage.summaries import MemberProvenance, SummaryView, compute_source_hash

pytestmark = pytest.mark.unit


def test_extractive_summarizer_cites_each_doc() -> None:
    summarizer = ExtractiveSummarizer()
    docs = [
        SummaryInput(
            "11111111-1111-1111-1111-111111111111", "CPI report", "CPI rose 0.2%. Details follow."
        ),
        SummaryInput(
            "22222222-2222-2222-2222-222222222222", "Fed decision", "The Fed held rates. More."
        ),
    ]
    result = summarizer.summarize(docs)
    assert "CPI rose 0.2%." in result.summary_text
    assert "The Fed held rates." in result.summary_text
    # Each claim cites the internal doc id it came from.
    assert result.citations["11111111-1111-1111-1111-111111111111"][0].startswith("CPI rose")
    assert result.citations["22222222-2222-2222-2222-222222222222"][0].startswith("The Fed held")
    assert result.token_cost > 0


def test_extractive_summarizer_deterministic() -> None:
    summarizer = ExtractiveSummarizer()
    docs = [
        SummaryInput("11111111-1111-1111-1111-111111111111", "T", "First sentence. Second one.")
    ]
    assert summarizer.summarize(docs) == summarizer.summarize(docs)


def test_extractive_summarizer_empty_doc_skipped() -> None:
    summarizer = ExtractiveSummarizer()
    result = summarizer.summarize([SummaryInput("id", None, "")])
    assert result.summary_text == ""
    assert result.citations == {}


def test_backend_factory_branches() -> None:
    assert get_summary_backend(Settings(llm_backend="none")) is None
    local = get_summary_backend(Settings(llm_backend="local"))
    assert isinstance(local, ExtractiveSummarizer)
    assert local.is_paid is False  # free → never gated by the spend cap (B21)


def test_api_backend_requires_key_and_is_paid() -> None:
    from atlascore.core.errors import ConfigError
    from atlascore.enrichment.summaries.hosted import HostedSummaryBackend

    # api without a key fails loudly (B21).
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        get_summary_backend(Settings(llm_backend="api", llm_api_key=None))
    # api with a key returns the hosted backend (no network at construction); it is metered.
    backend = get_summary_backend(
        Settings(llm_backend="api", llm_api_key="k")
    )  # pragma: allowlist secret
    assert isinstance(backend, HostedSummaryBackend)
    assert backend.is_paid is True


def test_source_hash_order_sensitive() -> None:
    a = compute_source_hash(["sha256:1", "sha256:2"])
    b = compute_source_hash(["sha256:2", "sha256:1"])
    assert a != b  # order is significant (part of the cache key)
    assert compute_source_hash(["sha256:1", "sha256:2"]) == a  # deterministic
    assert a.startswith("sha256:")


def test_summary_tool_registered_with_caps() -> None:
    server = build_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_GET_DOCUMENT_SUMMARY)
    props = tool.inputSchema["properties"]
    assert tool.inputSchema["required"] == ["document_ids"]
    assert props["document_ids"]["maxItems"] == SUMMARY_DOCS_MAX
    assert props["allow_compute"]["type"] == "boolean"
    assert tool.description is not None
    assert "untrusted evidence" in tool.description.lower()


def test_summary_payload_hit_and_miss() -> None:
    view = SummaryView(
        summary_id=UUID(int=1),
        scope="multi",
        summary_text="…",
        citations={"01J": ["claim"]},
        member_doc_ids=["a", "b"],
        source_hash="sha256:x",
        model_name="extractive",
        model_version="1.0.0",
        prompt_version="extractive@1",
        token_cost=42,
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
    )
    member = MemberProvenance(
        UUID(int=2), "Reuters", "https://x.test/a", datetime(2026, 6, 1, tzinfo=UTC)
    )
    hit = _summary_to_payload(SummaryOutcome(True, "hit", view, [member], ["a", "b"]))
    assert hit["cache"] == "hit"
    assert hit["scope"] == "multi"
    assert hit["provenance"]["prompt_version"] == "extractive@1"
    assert hit["provenance"]["member_sources"][0]["source"] == "Reuters"

    miss = _summary_to_payload(
        SummaryOutcome(
            False, "miss", None, [], ["a"], reason="LLM_BACKEND=none and no cached summary"
        )
    )
    assert miss["summary_text"] is None
    assert "LLM_BACKEND=none" in miss["reason"]
