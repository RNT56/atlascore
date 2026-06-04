"""Unit tests for the `atlas search` CLI surface (offline; no live DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app
from atlascore.cli.search import _as_utc
from atlascore.core.settings import get_settings

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_search_help_lists_options() -> None:
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    for token in ("--source", "--since", "--until", "--limit"):
        assert token in result.output


def test_search_exits_cleanly_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unreachable DB -> session_or_exit exits 1 with a clean message (not a traceback).
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/none")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["search", "rates"])
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)  # typer.Exit, not a crash
    finally:
        get_settings.cache_clear()


def test_search_rejects_bad_date() -> None:
    result = runner.invoke(app, ["search", "rates", "--since", "not-a-date"])
    assert result.exit_code == 2  # click rejects the malformed --since before any DB work


def test_as_utc_normalizes() -> None:
    assert _as_utc(None) is None
    naive = datetime(2026, 6, 4, 0, 0, 0)  # noqa: DTZ001 — deliberately naive CLI input
    assert _as_utc(naive) == datetime(2026, 6, 4, tzinfo=UTC)
    aware = datetime(2026, 6, 4, 5, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert _as_utc(aware) == datetime(2026, 6, 4, tzinfo=UTC)
