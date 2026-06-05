"""Unit tests for RawStore.delete + the backup pg_dump plan + the purge CLI guard (offline).

The purge cascade itself needs a DB and lives in the integration suite; here we cover the
dependency-free pieces: idempotent raw deletion, secret-safe pg_dump command construction, and the
destructive-confirmation guard on `atlas purge`.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from atlascore.cli.backup import build_pg_dump_plan
from atlascore.cli.main import app
from atlascore.storage.rawstore import RawStore

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_rawstore_delete_idempotent(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    blob = store.put(b"hello raw")
    assert store.exists(blob.content_hash)
    assert store.delete(blob.relpath) is True  # removed
    assert not store.exists(blob.content_hash)
    assert store.delete(blob.relpath) is False  # already gone → idempotent no-op


def test_pg_dump_plan_keeps_password_out_of_argv() -> None:
    plan = build_pg_dump_plan(
        "postgresql+psycopg://atlas:s3cr%40t@db.host:5433/atlascore", Path("out.dump")
    )
    assert plan.dbname == "atlascore"
    assert plan.argv[0] == "pg_dump"
    assert "--format=custom" in plan.argv
    host_at = plan.argv.index("--host")
    assert plan.argv[host_at : host_at + 2] == ["--host", "db.host"]
    assert "--port" in plan.argv and "5433" in plan.argv
    # The password is URL-decoded and ONLY in the env (never on the command line) - §14.
    assert plan.env["PGPASSWORD"] == "s3cr@t"
    assert not any("s3cr@t" in a for a in plan.argv)


def test_pg_dump_plan_requires_dbname() -> None:
    with pytest.raises(ValueError, match="no database name"):
        build_pg_dump_plan("postgresql+psycopg://u:p@h:5432/", Path("out.dump"))


def test_purge_cli_refuses_without_yes() -> None:
    result = runner.invoke(app, ["purge", str(UUID(int=1))])  # no --yes
    assert result.exit_code == 1
    assert "--yes" in result.output


def test_purge_cli_rejects_bad_uuid() -> None:
    result = runner.invoke(app, ["purge", "not-a-uuid", "--yes"])
    assert result.exit_code == 1
    assert "not a valid id" in result.output
