"""Unit tests for the digest rendering + `atlas health`/`digest` CLI wiring (offline; no DB)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from typer.testing import CliRunner

from atlascore.cli.health import _fmt_lag, build_digest
from atlascore.cli.main import app
from atlascore.mcp_server import TOOL_GET_SOURCE_HEALTH, build_mcp_server
from atlascore.storage.source_health import SourceHealthRow

pytestmark = pytest.mark.unit

runner = CliRunner()
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _row(slug: str, status: str, lag: timedelta | None, fails: int = 0) -> SourceHealthRow:
    return SourceHealthRow(
        source_id=UUID(int=1),
        source_name=f"{slug} name",
        source_slug=slug,
        status=status,
        last_attempt_at=NOW,
        last_success_at=NOW,
        last_item_published_at=NOW,
        freshness_lag=lag,
        consecutive_failures=fails,
        success_rate_24h=None,
        parse_quality=None,
        duplicate_rate=None,
        item_count_recent=0,
        updated_at=NOW,
    )


def test_fmt_lag() -> None:
    assert _fmt_lag(None) == "—"
    assert _fmt_lag(timedelta(days=10)) == "10d0h"
    assert _fmt_lag(timedelta(hours=5, minutes=30)) == "5h30m"
    assert _fmt_lag(timedelta(minutes=12)) == "12m"


def test_build_digest_lists_problem_sources() -> None:
    rows = [
        _row("stale_src", "stale", timedelta(days=10)),
        _row("fail_src", "cooling_down", None, 3),
    ]
    dlq = [("Failing Source", "fetch", 3)]
    text = build_digest(rows, dlq, fetch_worker_up=False, scheduler_up=False, now=NOW)
    assert "Sources needing attention (2)" in text
    assert "stale_src" in text and "stale" in text
    assert "fail_src" in text and "cooling_down" in text
    assert "DLQ depth: 3" in text
    assert "fetch worker NOT detected" in text
    assert "scheduler NOT detected" in text  # B19: real liveness, not a placeholder


def test_build_digest_all_ok() -> None:
    text = build_digest([], [], fetch_worker_up=True, scheduler_up=True, now=NOW)
    assert "all ok" in text
    assert "DLQ depth: 0" in text
    assert "fetch worker present" in text
    assert "scheduler present" in text


def test_health_and_digest_help() -> None:
    for cmd in ("health", "digest"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0


def test_get_source_health_registered_offline() -> None:
    server = build_mcp_server()  # no DB needed to list tools
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_GET_SOURCE_HEALTH)
    assert tool.inputSchema["properties"]["limit"]["maximum"] == 200
    assert tool.description is not None and "not the operational gate" in tool.description.lower()
