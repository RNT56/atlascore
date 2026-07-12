"""Unit tests for the fail-closed unattended-soak preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlascore.cli.soak import evaluate_soak_config
from atlascore.core.settings import Settings

pytestmark = pytest.mark.unit


def _tools(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in {"pg_dump", "pg_restore", "rclone"} else None


def test_preflight_accepts_complete_production_config(tmp_path: Path) -> None:
    settings = Settings(
        app_env="prod",
        database_url="postgresql+psycopg://atlascore@localhost/atlascore",
        raw_store_dir=tmp_path,
        default_user_agent="AtlasCore/0.1.0 (contact: operator@example.com)",
        ntfy_topic="atlascore-operator",
        heartbeat_url="https://healthchecks.example/ping/token",
    )
    checks = evaluate_soak_config(
        settings,
        environ={"BACKUP_DIR": "/backups", "BACKUP_OFFSITE_REMOTE": "remote:atlascore"},
        find_tool=_tools,
    )
    assert checks
    assert all(check.ok for check in checks)


def test_preflight_rejects_example_defaults(tmp_path: Path) -> None:
    settings = Settings(raw_store_dir=tmp_path / "missing")
    checks = evaluate_soak_config(settings, environ={}, find_tool=lambda _name: None)
    failed = {check.name for check in checks if not check.ok}
    assert {
        "environment",
        "database credentials",
        "user agent",
        "delivery",
        "heartbeat",
        "raw store",
        "pg_dump",
        "pg_restore",
        "backup directory",
        "offsite backup",
    } <= failed
