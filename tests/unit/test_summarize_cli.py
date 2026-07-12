"""Unit tests for the summary control-plane command."""

from __future__ import annotations

import pytest
from click import unstyle
from typer.testing import CliRunner

from atlascore.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_summarize_help_exposes_cache_control() -> None:
    result = runner.invoke(app, ["summarize", "--help"])
    assert result.exit_code == 0
    # GitHub Actions advertises a colour-capable terminal; Rich may place ANSI
    # styling sequences inside option names, unlike the default local runner.
    assert "--cache-only" in unstyle(result.output)


def test_summarize_rejects_invalid_uuid_before_database() -> None:
    result = runner.invoke(app, ["summarize", "not-a-uuid"])
    assert result.exit_code == 2
    assert "not valid UUIDs" in result.output
