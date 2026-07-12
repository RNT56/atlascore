"""Integration: a `raw_document` row indexes a stored blob; `content_hash` dedupes (B3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlascore.storage.postgres.models import RawDocument
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import load_source_yaml, upsert_source

pytestmark = pytest.mark.integration

FIX = Path("tests/fixtures/sources")


def test_raw_document_indexes_blob_and_dedupes(migrated_engine: Engine, tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    blob = store.put(b"<rss><item>hello</item></rss>")
    with Session(migrated_engine) as session:
        src = upsert_source(session, load_source_yaml(FIX / "approved_rss.yaml").to_record())
        session.flush()
        row = RawDocument(
            source_id=src.id,
            fetched_at=datetime(2026, 6, 3, tzinfo=UTC),
            content_hash=blob.content_hash,
            path=blob.relpath,
            size_bytes=blob.size_bytes,
            mime_type="application/rss+xml",
            origin_url="https://feed.example.test/rss",
            parser_ref=None,
            schema_version=1,
        )
        session.add(row)
        session.flush()
        assert row.id is not None
        assert row.id.version == 7  # UUIDv7 PK
        assert row.origin_url == "https://feed.example.test/rss"
        assert row.parser_ref is None

        # content_hash is the content address: a duplicate raw_document is rejected.
        duplicate = RawDocument(
            source_id=src.id,
            fetched_at=datetime(2026, 6, 3, tzinfo=UTC),
            content_hash=blob.content_hash,
            path=blob.relpath,
            schema_version=1,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
