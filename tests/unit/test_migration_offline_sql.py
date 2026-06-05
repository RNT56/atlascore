"""Offline SQL generation regression (no DB, no network).

``alembic upgrade head --sql`` must emit valid SQL for every migration. Migration 0010
(time_series_point yearly partitions) once crashed offline because it called
``op.get_bind().exec_driver_sql(...)`` — which has no live bind under offline mode's
MockConnection. The fix guards that introspection with ``op.get_context().as_sql``. This test
locks that in: the whole chain must render offline and the 0010 partition DDL must appear.
"""

from __future__ import annotations

import io

from alembic import command
from alembic.config import Config

import atlascore

pytestmark = __import__("pytest").mark.unit

_REPO_ROOT = __import__("pathlib").Path(atlascore.__file__).resolve().parents[2]


def _offline_sql() -> str:
    buf = io.StringIO()
    cfg = Config(str(_REPO_ROOT / "alembic.ini"), output_buffer=buf)
    # A dummy URL keeps env.py from resolving Settings/DATABASE_URL; offline mode never connects.
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://u:p@localhost/atlascore")
    command.upgrade(cfg, "head", sql=True)  # raises if any migration can't render offline
    return buf.getvalue()


def test_offline_upgrade_head_renders_without_a_live_bind() -> None:
    sql = _offline_sql()
    # 0010's yearly-partition DDL is the line that used to crash offline — it must now render.
    assert "PARTITION OF time_series_point" in sql
    assert "time_series_point_2026" in sql
    # And the migration chain reaches head (the last migration's job_type/spend ledger DDL).
    assert "llm_spend" in sql
