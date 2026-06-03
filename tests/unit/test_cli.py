"""Unit tests for the ``atlas`` CLI entry point."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "atlascore" in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "atlascore" in result.output


def test_help_lists_app() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AtlasCore" in result.output
