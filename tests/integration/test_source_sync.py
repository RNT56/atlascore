"""Integration: register source YAML into the `source` table (B2 done-when).

Skipped when no DB is reachable (see conftest). All writes run in a transaction that is
rolled back, leaving the target database unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from atlascore.schemas.source import PolicyStatus, SourceRecord
from atlascore.storage.postgres.models import Source
from atlascore.storage.sources import get_source, list_sources, load_source_yaml, upsert_source

pytestmark = pytest.mark.integration

FIX = Path("tests/fixtures/sources")


def _record(fixture: str) -> SourceRecord:
    return load_source_yaml(FIX / f"{fixture}.yaml").to_record()


def test_sync_registers_approved_source(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = upsert_source(session, _record("approved_rss"))
        session.flush()
        session.refresh(row)
        assert row.id is not None and row.id.version == 7  # UUIDv7 PK assigned
        assert row.policy_status == "approved"
        assert row.enabled is True
        assert row.config["slug"] == "approved_rss"
        assert row.config["allowed_uses"] == ["internal_search"]
        assert row.credential_ref is None
        # Lookup by slug then by name both resolve the row.
        assert get_source(session, "approved_rss") is row
        assert get_source(session, "Approved RSS") is row
        session.rollback()


def test_sync_is_idempotent_on_name(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        first = upsert_source(session, _record("approved_rss"))
        session.flush()
        first_id = first.id
        second = upsert_source(session, _record("approved_rss"))
        session.flush()
        assert second.id == first_id  # same row, not a duplicate
        count = session.scalar(
            select(func.count()).select_from(Source).where(Source.name == "Approved RSS")
        )
        assert count == 1
        session.rollback()


def test_sync_rename_updates_same_row(migrated_engine: Engine) -> None:
    # Keyed on the stable slug: renaming (same slug, new name) updates the row, no orphan.
    with Session(migrated_engine) as session:
        record = _record("approved_rss")
        first = upsert_source(session, record)
        session.flush()
        first_id = first.id
        renamed = record.model_copy(update={"name": "Approved RSS (renamed)"})
        second = upsert_source(session, renamed)
        session.flush()
        assert second.id == first_id
        assert second.name == "Approved RSS (renamed)"
        slug_count = session.scalar(
            select(func.count())
            .select_from(Source)
            .where(Source.config["slug"].astext == "approved_rss")
        )
        assert slug_count == 1
        session.rollback()


def test_registered_disabled_source_is_not_fetchable(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = upsert_source(session, _record("disabled_pending"))
        session.flush()
        fetchable = row.enabled and PolicyStatus(row.policy_status).is_fetchable
        assert fetchable is False  # default-deny: disabled + pending
        session.rollback()


def test_list_sources_returns_registered(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        upsert_source(session, _record("approved_rss"))
        upsert_source(session, _record("example_rss"))
        session.flush()
        names = {s.name for s in list_sources(session)}
        assert {"Approved RSS", "Example RSS Source"} <= names
        session.rollback()
