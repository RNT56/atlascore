"""The audited erasure path — `atlas purge` (R3, CLAUDE.md §16, runbook §6).

This is the **single** exception to raw-store immutability. Given a document id it runs the full
cascade (embeddings → mentions → summaries incl. multi-doc → document → raw file+row → alert
evidence reconciliation), then writes a **retained** `audit_log` entry (`retention_class='purge'`)
that records the action + content hashes only — never the personal data. Given an entity id it
deletes the entity + its mentions (documents survive). Purge is **idempotent** (a missing target is
a clean no-op).

FK ordering note: all FKs are `RESTRICT`, so the delete order is DB-enforced (children before
parents). **One `raw_document` is shared by every `document` parsed from the same fetch** (one feed
fetch → N per-GUID documents → one raw), so the raw file/row is deleted **only when the last
referencing document is gone** — RESTRICT forbids otherwise. Until then a shared raw still holds the
purged item's bytes among its siblings; full raw erasure happens when its last document is purged
(documented honest limitation — runbook §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Delete, Update, delete, func, or_, select, update
from sqlalchemy.orm import Session

from atlascore.schemas.alert import AlertStatus
from atlascore.schemas.audit_log import Actor, RetentionClass
from atlascore.storage.audit import write_audit_log
from atlascore.storage.postgres.models import (
    Alert,
    Document,
    Embedding,
    Entity,
    Mention,
    RawDocument,
    Summary,
)
from atlascore.storage.rawstore import RawStore

PURGE_ACTION = "purge"


def _rowcount(session: Session, stmt: Delete | Update) -> int:
    """Execute a DML statement and return its affected-row count (typed, matching queue.py)."""
    return cast("CursorResult[Any]", session.execute(stmt)).rowcount


@dataclass(frozen=True)
class PurgeResult:
    """What an `atlas purge` run erased. ``found=False`` means the id was already gone (no-op)."""

    target_kind: str  # document | entity | unknown
    found: bool
    embeddings: int = 0
    mentions: int = 0
    summaries: int = 0
    documents: int = 0
    raw_deleted: bool = False
    alerts_reconciled: int = 0
    alerts_purged: int = 0
    entities: int = 0
    documents_reconciled: int = 0


def _reconcile_alert_evidence(session: Session, doc_id: UUID) -> tuple[int, int]:
    """Remove ``doc_id`` from every alert's evidence; an alert losing ALL evidence ⇒ purged."""
    sid = str(doc_id)
    alerts = list(session.scalars(select(Alert).where(Alert.evidence_doc_ids.contains([sid]))))
    purged = 0
    for alert in alerts:
        alert.evidence_doc_ids = [e for e in alert.evidence_doc_ids if e != sid]
        if not alert.evidence_doc_ids:
            alert.status = AlertStatus.PURGED.value
            purged += 1
    return len(alerts), purged


def purge_document(session: Session, raw_store: RawStore, doc_id: UUID) -> PurgeResult:
    """Run the full R3 erasure cascade for one document. Caller owns the transaction.

    The audit entry's ``ts`` is the authoritative DB time (``server_default=now()``), so no clock is
    injected here.
    """
    doc = session.get(Document, doc_id)
    if doc is None:
        return PurgeResult(target_kind="document", found=False)

    raw_document_id = doc.raw_document_id
    doc_content_hash = doc.content_hash

    # 1-2. derived enrichment rows
    embeddings = _rowcount(session, delete(Embedding).where(Embedding.document_id == doc_id))
    mentions = _rowcount(session, delete(Mention).where(Mention.document_id == doc_id))
    # 3. summaries: single (document_id) AND multi (member_doc_ids[] contains the id - GIN scan).
    summaries = _rowcount(
        session,
        delete(Summary).where(
            or_(Summary.document_id == doc_id, Summary.member_doc_ids.contains([str(doc_id)]))
        ),
    )
    # 7. reconcile alert evidence (before the doc row is gone - order is immaterial, no FK).
    alerts_reconciled, alerts_purged = _reconcile_alert_evidence(session, doc_id)
    # 4-5. drop the document (removes it from FTS - the tsvector index is on this row).
    session.execute(delete(Document).where(Document.id == doc_id))
    session.flush()

    # 6. raw file + row — ONLY if no sibling document still references this shared raw.
    raw_deleted = False
    raw_content_hash: str | None = None
    still_referenced = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.raw_document_id == raw_document_id)
    )
    if not still_referenced:
        raw = session.get(RawDocument, raw_document_id)
        if raw is not None:
            raw_content_hash = raw.content_hash
            raw_store.delete(raw.path)
            session.execute(delete(RawDocument).where(RawDocument.id == raw_document_id))
            raw_deleted = True

    # 8. retained audit entry — hashes + counts only, never the personal data.
    write_audit_log(
        session,
        action=PURGE_ACTION,
        actor=Actor.CLI,
        target_ref=str(doc_id),
        retention_class=RetentionClass.PURGE,
        detail={
            "kind": "document",
            "document_content_hash": doc_content_hash,
            "raw_content_hash": raw_content_hash,
            "raw_deleted": raw_deleted,
            "embeddings": embeddings,
            "mentions": mentions,
            "summaries": summaries,
            "alerts_reconciled": alerts_reconciled,
            "alerts_purged": alerts_purged,
        },
    )
    return PurgeResult(
        target_kind="document",
        found=True,
        embeddings=embeddings,
        mentions=mentions,
        summaries=summaries,
        documents=1,
        raw_deleted=raw_deleted,
        alerts_reconciled=alerts_reconciled,
        alerts_purged=alerts_purged,
    )


def purge_entity(session: Session, entity_id: UUID) -> PurgeResult:
    """Erase an entity + its mentions; documents survive (R3, runbook §6.2). Caller owns the txn."""
    entity = session.get(Entity, entity_id)
    if entity is None:
        return PurgeResult(target_kind="entity", found=False)

    mentions = _rowcount(session, delete(Mention).where(Mention.entity_id == entity_id))
    # Reconcile the non-referential document.entity_ids arrays (drop the purged id).
    reconciled = _rowcount(
        session,
        update(Document)
        .where(Document.entity_ids.contains([str(entity_id)]))
        .values(entity_ids=func.array_remove(Document.entity_ids, str(entity_id))),
    )
    session.execute(delete(Entity).where(Entity.id == entity_id))

    write_audit_log(
        session,
        action=PURGE_ACTION,
        actor=Actor.CLI,
        target_ref=str(entity_id),
        retention_class=RetentionClass.PURGE,
        detail={
            "kind": "entity",
            "mentions": mentions,
            "documents_reconciled": reconciled,
        },
    )
    return PurgeResult(
        target_kind="entity",
        found=True,
        mentions=mentions,
        entities=1,
        documents_reconciled=reconciled,
    )


def purge_target(session: Session, raw_store: RawStore, target_id: UUID) -> PurgeResult:
    """Dispatch a purge: a document if one exists, else an entity, else a no-op. Caller owns txn."""
    if session.get(Document, target_id) is not None:
        return purge_document(session, raw_store, target_id)
    if session.get(Entity, target_id) is not None:
        return purge_entity(session, target_id)
    return PurgeResult(target_kind="unknown", found=False)
