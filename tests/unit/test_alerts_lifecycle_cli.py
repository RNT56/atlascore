"""Unit tests for the B18 alert lifecycle CLI guards (offline; no DB)."""

from __future__ import annotations

from uuid import UUID

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_ack_bad_uuid_exits_1() -> None:
    result = runner.invoke(app, ["alerts", "ack", "not-a-uuid"])
    assert result.exit_code == 1
    assert "not a valid alert id" in result.output


def test_resolve_bad_uuid_exits_1() -> None:
    result = runner.invoke(app, ["alerts", "resolve", "nope"])
    assert result.exit_code == 1


def test_alerts_help_lists_lifecycle_commands() -> None:
    result = runner.invoke(app, ["alerts", "--help"])
    assert result.exit_code == 0
    for cmd in ("run", "ack", "resolve", "list"):
        assert cmd in result.output


def test_ack_valid_uuid_db_unreachable_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    # A well-formed id with no DB reachable should exit cleanly (session_or_exit handles it).
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/none")
    from atlascore.core.settings import get_settings

    get_settings.cache_clear()
    result = runner.invoke(app, ["alerts", "ack", str(UUID(int=1))])
    get_settings.cache_clear()
    assert result.exit_code == 1
