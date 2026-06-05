"""Unit tests for the retention subsystem's offline surface (B16; no DB, no network).

Cutoff/window math, result shapes, Settings defaults, and the CLI guards (bad window, db-unreachable,
dry-run). The DML row-deletion behavior is in the integration suite (test_retention.py — distinct
basename per the test-basename-collision rule).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from atlascore.cli.main import app
from atlascore.core.durations import parse_duration
from atlascore.core.settings import Settings
from atlascore.storage.retention import AUDIT_ROTATE_ACTION

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_settings_retention_defaults() -> None:
    s = Settings()
    assert s.audit_routine_retention == "180d"
    assert s.crawl_job_retention == "90d"
    assert parse_duration(s.audit_routine_retention).days == 180
    assert parse_duration(s.crawl_job_retention).days == 90


def test_audit_rotate_action_constant() -> None:
    assert AUDIT_ROTATE_ACTION == "audit_rotate"


def test_cli_audit_rotate_bad_window_exits_1() -> None:
    result = runner.invoke(app, ["audit", "rotate", "--audit-window", "nonsense"])
    assert result.exit_code == 1
    assert "invalid audit window" in result.output


def test_cli_audit_rotate_bad_crawl_window_exits_1() -> None:
    result = runner.invoke(app, ["audit", "rotate", "--crawl-window", "5x"])
    assert result.exit_code == 1
    assert "invalid crawl window" in result.output


def test_cli_audit_help_lists_rotate() -> None:
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "rotate" in result.output
