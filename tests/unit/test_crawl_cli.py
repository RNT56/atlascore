"""Unit tests for the `atlas crawl` CLI surface (offline; no live DB)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app
from atlascore.core.settings import get_settings

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_crawl_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["crawl", "--help"])
    assert result.exit_code == 0
    assert "enqueue" in result.output
    assert "dlq" in result.output
    assert "requeue" in result.output


def test_requeue_requires_target() -> None:
    # No job_id and no --all → usage error (exit 2), before any DB access.
    result = runner.invoke(app, ["crawl", "requeue"])
    assert result.exit_code == 2
    assert "specify a <job_id>" in result.output


def test_requeue_bulk_needs_yes() -> None:
    # --all without --yes is refused (exit 1) before any DB access.
    result = runner.invoke(app, ["crawl", "requeue", "--all"])
    assert result.exit_code == 1
    assert "--yes" in result.output


def test_requeue_invalid_job_id() -> None:
    result = runner.invoke(app, ["crawl", "requeue", "not-a-uuid"])
    assert result.exit_code == 2
    assert "invalid job id" in result.output


def test_crawl_enqueue_exits_cleanly_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    # Point at a guaranteed-unreachable DB so session_or_exit exits 1 with a clean message
    # (not an uncaught traceback), independent of any ambient local Postgres.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/none")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["crawl", "enqueue", "whatever"])
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)  # typer.Exit, not a crash
    finally:
        get_settings.cache_clear()
