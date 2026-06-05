"""Integration: the B1 migration applies to a real Postgres 16 + pgvector, and the
``source`` table behaves (server defaults, uuid7 PK, default-deny, CHECK constraints).

This is the automated form of B1's "migrations run" done-when. Skipped when no DB is
reachable (see conftest). Mutations run inside a transaction and are rolled back, so the
target database is left unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlascore.schemas.source import AccessMethod
from atlascore.storage.postgres.models import Source

pytestmark = pytest.mark.integration

# `migrated_engine` (apply migrations to head) is provided by tests/integration/conftest.py.

EXPECTED_COLUMNS = {
    "id",
    "name",
    "category",
    "access_method",
    "base_url",
    "policy_status",
    "rate_limit",
    "retention",
    "freshness",
    "config",
    "checkpoint",  # B20: runtime-owned incremental crawl cursor (migration 0011)
    "parser_ref",
    "credential_ref",
    "enabled",
    "schema_version",
    "created_at",
    "updated_at",
}


def test_required_extensions_present(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        installed = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    assert {"vector", "pg_trgm"} <= installed


def test_source_columns_match_model(migrated_engine: Engine) -> None:
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("source")}
    assert columns == EXPECTED_COLUMNS


def test_source_insert_applies_defaults(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = Source(
            name="b1-integration-test-source",
            category="news_rss",
            access_method=AccessMethod.RSS.value,
        )
        session.add(src)
        session.flush()
        session.refresh(src)
        # uuid7 client default applied as the PK.
        assert src.id is not None
        assert src.id.version == 7
        # Default-deny + jsonb/timestamp server defaults applied by the DB.
        assert src.policy_status == "unknown"
        assert src.enabled is False
        assert src.rate_limit == {}
        assert src.config == {}
        assert src.schema_version == 1
        assert src.created_at is not None
        session.rollback()  # leave the DB untouched


def test_check_constraint_rejects_bad_access_method(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            Source(
                name="b1-integration-test-bad-method",
                category="news_rss",
                access_method="ftp",  # not in the access_method CHECK enum
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
