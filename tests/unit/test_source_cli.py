"""Unit tests for `atlas source validate` (pure; no DB)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()
FIX = "tests/fixtures/sources"


def test_validate_approved_ok() -> None:
    result = runner.invoke(app, ["source", "validate", f"{FIX}/example_rss.yaml"])
    assert result.exit_code == 0
    assert "fetchable" in result.output


def test_validate_disabled_pending_ok() -> None:
    result = runner.invoke(app, ["source", "validate", f"{FIX}/disabled_pending.yaml"])
    assert result.exit_code == 0
    assert "disabled" in result.output


@pytest.mark.parametrize(
    "fixture",
    ["pending_enabled", "unknown_enabled", "prohibited_enabled", "bad_duration", "bad_cron"],
)
def test_validate_refuses_invalid(fixture: str) -> None:
    result = runner.invoke(app, ["source", "validate", f"{FIX}/{fixture}.yaml"])
    assert result.exit_code == 1


def test_validate_all_scans_registry() -> None:
    result = runner.invoke(app, ["source", "validate", "--all"])
    assert result.exit_code == 0
    assert "fed_press_releases" in result.output


def test_validate_requires_path_or_all() -> None:
    result = runner.invoke(app, ["source", "validate"])
    assert result.exit_code == 2


def test_validate_rejects_path_and_all_together() -> None:
    result = runner.invoke(app, ["source", "validate", f"{FIX}/example_rss.yaml", "--all"])
    assert result.exit_code == 2


def test_validate_missing_path_fails_cleanly() -> None:
    result = runner.invoke(app, ["source", "validate", "/no/such/source.yaml"])
    assert result.exit_code == 1
    # Clean handling (FAIL line), not an uncaught traceback.
    assert not isinstance(result.exception, FileNotFoundError)
