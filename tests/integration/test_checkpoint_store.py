"""Integration: B20 source.checkpoint store round-trip on Postgres 16 (migration 0011)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.storage.checkpoints import get_checkpoint, set_checkpoint
from atlascore.storage.postgres.models import Source

pytestmark = pytest.mark.integration

SLUG = "checkpoint_it"


def _cleanup(session: Session) -> None:
    session.execute(delete(Source).where(Source.config["slug"].astext.like(f"{SLUG}%")))


def _source(session: Session) -> Source:
    s = Source(
        name=f"{SLUG}-src",
        category="security_cve",
        access_method="api",
        policy_status="approved",
        enabled=True,
        config={"slug": SLUG},
        schema_version=1,
    )
    session.add(s)
    session.flush()
    return s


def test_checkpoint_round_trip_and_merge(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        src = _source(session)
        sid = src.id

    # Unset → empty.
    with Session(migrated_engine) as session:
        assert get_checkpoint(session, sid) == {}

    # Set, then read back.
    with Session(migrated_engine) as session, session.begin():
        set_checkpoint(session, sid, {"lastModStartDate": "2026-06-01T00:00:00.000"})
    with Session(migrated_engine) as session:
        assert get_checkpoint(session, sid) == {"lastModStartDate": "2026-06-01T00:00:00.000"}

    # Merge-update: a second key is added, the first preserved.
    with Session(migrated_engine) as session, session.begin():
        set_checkpoint(session, sid, {"startIndex": "100"})
    with Session(migrated_engine) as session:
        cp = get_checkpoint(session, sid)
        assert cp == {"lastModStartDate": "2026-06-01T00:00:00.000", "startIndex": "100"}

    # The column is NOT touched by the YAML config (distinct from source.config).
    with Session(migrated_engine) as session:
        row = session.scalar(select(Source).where(Source.id == sid))
        assert row is not None
        assert "checkpoint" not in (row.config or {})  # cursor lives in its own column

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
