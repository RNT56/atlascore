"""Integration: B14 `atlas purge` full R3 cascade against Postgres 16.

The B14 done-when (purge half): purge works and is audited. Exercises the full document cascade
(embeddings → mentions → summaries incl. the **multi-doc summary regression** → document → raw →
alert-evidence reconciliation → retained audit entry), the **shared-raw guard** (a raw is deleted
only when its last referencing document is purged), idempotency, and the entity purge.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import (
    Alert,
    AuditLog,
    Document,
    Embedding,
    Entity,
    Mention,
    RawDocument,
    Source,
    Summary,
)
from atlascore.storage.postgres.models.embedding import EMBEDDING_DIM
from atlascore.storage.purge import purge_document, purge_entity, purge_target
from atlascore.storage.rawstore import RawStore
from atlascore.storage.summaries import insert_summary

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SLUG = "purge_it"


def _cleanup(session: Session) -> None:
    src_ids = list(session.scalars(select(Source.id).where(Source.config["slug"].astext == SLUG)))
    if not src_ids:
        return
    doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(src_ids))))
    str_ids = [str(d) for d in doc_ids]
    session.execute(delete(Alert).where(Alert.dedupe_key.like(f"{SLUG}%")))
    if doc_ids:
        session.execute(delete(Embedding).where(Embedding.document_id.in_(doc_ids)))
        session.execute(delete(Mention).where(Mention.document_id.in_(doc_ids)))
        session.execute(
            delete(Summary).where(
                Summary.document_id.in_(doc_ids) | Summary.member_doc_ids.op("&&")(str_ids)
            )
        )
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))
    session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
    session.execute(delete(Entity).where(Entity.canonical_name == "PurgeCo"))
    session.execute(delete(Source).where(Source.id.in_(src_ids)))


def _doc(session: Session, source: Source, raw: RawDocument, n: int) -> UUID:
    result = upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://purge.test/{n}",
            title=f"Doc {n}",
            body_text=f"Body {n} with personal data.",
            published_at=datetime(2026, 6, 5, 18, n, tzinfo=UTC),
            content_hash=f"sha256:purgedoc{n}",
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )
    return result.document_id


def _seed(session: Session, store: RawStore) -> dict[str, UUID]:
    source = Source(
        name="purge-it-source",
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
        config={"slug": SLUG},
        schema_version=1,
    )
    session.add(source)
    session.flush()

    # raw1 is SHARED by docA + docB (one feed → many docs); raw2 backs only docC.
    blob1 = store.put(b"raw feed 1 with item A and item B")
    raw1 = RawDocument(
        source_id=source.id, content_hash=blob1.content_hash, path=blob1.relpath, schema_version=1
    )
    blob2 = store.put(b"raw feed 2 with item C")
    raw2 = RawDocument(
        source_id=source.id, content_hash=blob2.content_hash, path=blob2.relpath, schema_version=1
    )
    session.add_all([raw1, raw2])
    session.flush()

    doc_a = _doc(session, source, raw1, 0)
    doc_b = _doc(session, source, raw1, 1)
    doc_c = _doc(session, source, raw2, 2)

    # Derived data hanging off doc_a.
    session.add(
        Embedding(
            document_id=doc_a,
            chunk_index=0,
            model_name="hashing",
            model_version="1.0.0",
            dim=EMBEDDING_DIM,
            vector=[0.0] * EMBEDDING_DIM,
            source_hash="sha256:purgedoc0",
        )
    )
    entity = Entity(
        entity_type="company",
        canonical_name="PurgeCo",
        aliases=["purgeco"],
        identifiers={},
        schema_version=1,
    )
    session.add(entity)
    session.flush()
    session.add(
        Mention(
            entity_id=entity.id,
            document_id=doc_a,
            mentioned_at=NOW,
            model_name="rule-ner",
            model_version="1.0.0",
            context_snippet="…PurgeCo…",
        )
    )
    doc_a_obj = session.get(Document, doc_a)
    assert doc_a_obj is not None
    doc_a_obj.entity_ids = [str(entity.id)]

    # A single-doc summary of doc_a AND a multi-doc summary [doc_a, doc_c] (document_id=NULL —
    # the load-bearing R3 regression: it links members only via member_doc_ids[]).
    insert_summary(
        session,
        doc_ids=[doc_a],
        source_hash="sha256:single",
        model_name="extractive",
        model_version="1.0.0",
        prompt_version="extractive@1",
        summary_text="single summary of doc A (personal data)",
        citations={str(doc_a): ["claim"]},
        token_cost=5,
    )
    insert_summary(
        session,
        doc_ids=[doc_a, doc_c],
        source_hash="sha256:multi",
        model_name="extractive",
        model_version="1.0.0",
        prompt_version="extractive@1",
        summary_text="multi summary of A+C (personal data)",
        citations={str(doc_a): ["claim"], str(doc_c): ["claim"]},
        token_cost=9,
    )

    # alert1 cites only doc_a (→ purged when doc_a goes); alert2 cites doc_a + doc_c (→ keeps doc_c).
    session.add_all(
        [
            Alert(
                alert_type="sec_filing",
                rule_id="r",
                severity="low",
                status="open",
                title="A only",
                dedupe_key=f"{SLUG}:a-only",
                evidence_doc_ids=[str(doc_a)],
                schema_version=1,
            ),
            Alert(
                alert_type="sec_filing",
                rule_id="r",
                severity="low",
                status="open",
                title="A and C",
                dedupe_key=f"{SLUG}:a-and-c",
                evidence_doc_ids=[str(doc_a), str(doc_c)],
                schema_version=1,
            ),
        ]
    )
    return {
        "a": doc_a,
        "b": doc_b,
        "c": doc_c,
        "raw1": raw1.id,
        "raw2": raw2.id,
        "entity": entity.id,
    }


def test_purge_document_full_cascade(migrated_engine: Engine, tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        ids = _seed(session, store)

    with Session(migrated_engine) as session:
        raw1 = session.get(RawDocument, ids["raw1"])
        assert raw1 is not None
        raw1_relpath = raw1.path
    assert (tmp_path / raw1_relpath).exists()  # raw blob is on disk before the purge

    # Purge doc_a.
    with Session(migrated_engine) as session, session.begin():
        result = purge_document(session, store, ids["a"])
    assert result.found
    assert result.embeddings == 1
    assert result.mentions == 1
    assert result.summaries == 2  # single + multi (the multi-doc regression)
    assert result.documents == 1
    assert result.raw_deleted is False  # raw1 is shared with doc_b → retained
    assert result.alerts_reconciled == 2
    assert result.alerts_purged == 1

    with Session(migrated_engine) as session:
        assert session.get(Document, ids["a"]) is None
        assert session.get(Document, ids["b"]) is not None  # sibling survives
        assert session.get(Document, ids["c"]) is not None
        assert session.get(RawDocument, ids["raw1"]) is not None  # shared raw retained
        assert (tmp_path / raw1_relpath).exists()  # shared raw file retained
        # No derived rows for doc_a remain — including the multi-doc summary.
        assert (
            session.scalar(
                select(func.count()).select_from(Embedding).where(Embedding.document_id == ids["a"])
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Mention).where(Mention.document_id == ids["a"])
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Summary)
                .where(
                    (Summary.document_id == ids["a"])
                    | Summary.member_doc_ids.op("&&")([str(ids["a"])])
                )
            )
            == 0
        )
        # alert1 lost all evidence → purged; alert2 kept doc_c → still open.
        a_only = session.scalar(select(Alert).where(Alert.dedupe_key == f"{SLUG}:a-only"))
        a_and_c = session.scalar(select(Alert).where(Alert.dedupe_key == f"{SLUG}:a-and-c"))
        assert a_only is not None and a_only.status == "purged" and a_only.evidence_doc_ids == []
        assert a_and_c is not None and a_and_c.status == "open"
        assert a_and_c.evidence_doc_ids == [str(ids["c"])]
        # Retained audit entry — proof of erasure, hashes only (no personal data).
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "purge", AuditLog.target_ref == str(ids["a"]))
        )
        assert audit is not None
        assert audit.retention_class == "purge"
        assert audit.detail["document_content_hash"] == "sha256:purgedoc0"
        assert "personal data" not in str(audit.detail)

    # Idempotent: re-purging doc_a is a clean no-op, no second audit row.
    with Session(migrated_engine) as session, session.begin():
        again = purge_document(session, store, ids["a"])
    assert again.found is False
    with Session(migrated_engine) as session:
        n_audit = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "purge", AuditLog.target_ref == str(ids["a"]))
        )
        assert n_audit == 1

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_purge_shared_raw_deleted_on_last_reference(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    store = RawStore(tmp_path)
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        ids = _seed(session, store)
    with Session(migrated_engine) as session:
        raw1 = session.get(RawDocument, ids["raw1"])
        assert raw1 is not None
        raw1_relpath = raw1.path

    # Purge doc_a → raw1 retained (doc_b still references it).
    with Session(migrated_engine) as session, session.begin():
        r1 = purge_document(session, store, ids["a"])
    assert r1.raw_deleted is False
    assert (tmp_path / raw1_relpath).exists()

    # Purge doc_b → now the last reference to raw1 is gone → raw file + row deleted.
    with Session(migrated_engine) as session, session.begin():
        r2 = purge_document(session, store, ids["b"])
    assert r2.raw_deleted is True
    with Session(migrated_engine) as session:
        assert session.get(RawDocument, ids["raw1"]) is None
    assert not (tmp_path / raw1_relpath).exists()  # raw blob finally erased

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_purge_entity_keeps_documents(migrated_engine: Engine, tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        ids = _seed(session, store)

    with Session(migrated_engine) as session, session.begin():
        result = purge_entity(session, ids["entity"])
    assert result.found and result.mentions == 1 and result.documents_reconciled == 1

    with Session(migrated_engine) as session:
        assert session.get(Entity, ids["entity"]) is None
        # The document survives, with the entity id removed from its array.
        doc_a = session.get(Document, ids["a"])
        assert doc_a is not None
        assert str(ids["entity"]) not in doc_a.entity_ids
        assert (
            session.scalar(
                select(func.count()).select_from(Mention).where(Mention.entity_id == ids["entity"])
            )
            == 0
        )

    # purge_target dispatches an unknown id to a clean no-op.
    with Session(migrated_engine) as session, session.begin():
        miss = purge_target(session, store, UUID(int=999))
    assert miss.found is False and miss.target_kind == "unknown"

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
