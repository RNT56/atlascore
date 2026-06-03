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
