"""The parse worker (docs/architecture, docs/connectors §3.4-§3.5).

Consumes the ``parse`` jobs the fetch worker (B4) enqueues — one per feed GUID, payload
``{guid, link, title, raw_document_id}`` — turns the immutable raw into canonical
``document`` rows, and upserts them (URL + content-hash dedupe, last-write-wins R2).

Unlike fetch, parse does **no source I/O** (it reads only the local raw store + DB), so it
needs no per-host rate limit and no single-worker guard — parse may scale out via
``SELECT … FOR UPDATE SKIP LOCKED``. Claim and process run in SEPARATE transactions so a
crash can't lose the claim and ``fail()`` always runs in a clean transaction.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk.base import OutputKind
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import ConfigError
from atlascore.core.hashing import content_hash
from atlascore.core.logging import get_logger
from atlascore.parsers.article import ArticleParser
from atlascore.schemas import SCHEMA_VERSION
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.document import DocumentRecord
from atlascore.security.sanitization import strip_html
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import CrawlJob, Document, RawDocument, Source
from atlascore.storage.queue import claim_next, complete, enqueue, fail
from atlascore.storage.rawstore import RawStore
from atlascore.worker.run import load_connector

_log = get_logger("atlascore.worker.parse")


@dataclass(frozen=True)
class ParseJobResult:
    documents_created: int
    documents_updated: int
    documents_unchanged: int
    error: str | None = None


class _ParsedLocalContent(Protocol):
    body_text: str
    parser_version: str


class _LocalContentParser(Protocol):
    def parse(self, data: bytes, *, content_type: str | None = None) -> _ParsedLocalContent: ...


def _load_local_content_parser(ref: str) -> _LocalContentParser:
    module_path, _, class_name = ref.partition(":")
    if not module_path or not class_name:
        raise ConfigError(f"invalid local parser ref {ref!r} (expected 'module.path:ClassName')")
    cls = getattr(importlib.import_module(module_path), class_name)
    return cast("_LocalContentParser", cls())


def build_fetched_entry_record(
    source: Source,
    raw_doc: RawDocument,
    raw_bytes: bytes,
    *,
    payload: dict[str, object],
    clock: Clock,
    parser: _LocalContentParser | None = None,
) -> DocumentRecord:
    """Parse a secondary entry payload stored by the fetch worker, with no source I/O."""
    parser_ref = str(payload.get("parser_ref") or raw_doc.parser_ref or "")
    if not parser_ref:
        raise ConfigError("secondary parse job has no parser_ref")
    local_parser = parser or _load_local_content_parser(parser_ref)
    parsed = local_parser.parse(raw_bytes, content_type=raw_doc.mime_type)
    guid = str(payload.get("guid") or raw_doc.id)
    title_value = payload.get("title")
    title = strip_html(str(title_value)) if title_value else None
    link_value = payload.get("link") or raw_doc.origin_url
    canonical_url = str(link_value) if link_value else None
    published_value = payload.get("published_at")
    published = datetime.fromisoformat(str(published_value)) if published_value else None
    fingerprint = f"{guid}\n{title or ''}\n\n{parsed.body_text}".encode()
    return DocumentRecord(
        source_id=source.id,
        raw_document_id=raw_doc.id,
        canonical_url=canonical_url,
        title=title,
        body_text=parsed.body_text or None,
        published_at=published,
        parsed_at=clock.now(),
        content_hash=content_hash(fingerprint),
        parser_version=parsed.parser_version,
        schema_version=SCHEMA_VERSION,
    )


def build_document_records(
    source: Source,
    raw_doc: RawDocument,
    raw_bytes: bytes,
    *,
    clock: Clock,
    only_guid: str | None = None,
) -> list[DocumentRecord]:
    """Normalize a raw feed into ``DocumentRecord``s (the parse → normalize stages).

    ``only_guid`` restricts to a single feed item (the per-GUID parse-job path); ``None``
    yields every item (the ``atlas parse <raw_id>`` re-parse path). Works for any
    **document** connector (RSS in B5; SEC + NVD in B10) — each implements ``parse_entries``
    and the shared ``ArticleParser`` turns the item content into a sanitized ``document`` body.
    Time-series connectors (FRED) take the macro path and never reach here.
    """
    connector_ref = (source.config or {}).get("connector")
    if not connector_ref:
        raise ConfigError(f"source {source.name} has no fetch.connector configured")
    connector = load_connector(connector_ref)
    if connector.output_kind != OutputKind.DOCUMENT:
        raise NotImplementedError(
            f"connector {connector_ref} produces {connector.output_kind.value}, not documents"
        )

    entries = connector.parse_entries(raw_bytes)
    if only_guid is not None:
        entries = [e for e in entries if e.guid == only_guid]

    parser = ArticleParser()
    now = clock.now()
    records: list[DocumentRecord] = []
    for entry in entries:
        parsed = parser.parse(
            entry.content_html or entry.summary,
            fallback_title=entry.title,
            fallback_url=entry.link,
            fallback_published=entry.published,
        )
        # Fingerprint the parsed content. The feed GUID is folded in so two DISTINCT items
        # that happen to share an identical title+body (and have no canonical_url to dedupe on)
        # are not collapsed into one document by the null-URL dedupe (review finding 5). The
        # GUID is stable per item, so it never causes spurious re-parse churn.
        fingerprint = f"{entry.guid}\n{parsed.title or ''}\n\n{parsed.body_text}".encode()
        records.append(
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw_doc.id,
                canonical_url=parsed.canonical_url,
                title=parsed.title,
                body_text=parsed.body_text or None,
                language=parsed.language,
                published_at=parsed.published_at,
                parsed_at=now,
                content_hash=content_hash(fingerprint),
                parser_version=parser.version,
                schema_version=SCHEMA_VERSION,
            )
        )
    return records


def _persist(session: Session, records: list[DocumentRecord], *, clock: Clock) -> ParseJobResult:
    now = clock.now()
    created = updated = unchanged = 0
    for record in records:
        result = upsert_document(session, record, now=now)
        if result.outcome == "created":
            created += 1
        elif result.outcome == "updated":
            updated += 1
        else:
            unchanged += 1
        # Drive the pipeline forward: a created/changed document gets an idempotent enrich job
        # (language → NER → topics → embeddings, B11). The content_hash in the dedupe_key means an
        # unchanged re-parse never re-enqueues, and a changed body re-enriches (last-write-wins).
        if result.changed:
            enqueue(
                session,
                source_id=record.source_id,
                job_type=JobType.ENRICH,
                dedupe_key=f"enrich:{result.document_id}:{record.content_hash}",
                scheduled_at=now,
                payload={"document_id": str(result.document_id)},
            )
    return ParseJobResult(created, updated, unchanged)


def run_parse_job(
    session: Session,
    job: CrawlJob,
    *,
    raw_store: RawStore,
    clock: Clock,
) -> ParseJobResult:
    """Run one parse job: read raw → parse the item by GUID → upsert document(s)."""
    payload = job.payload or {}
    raw_id = payload.get("raw_document_id")
    if not raw_id:
        raise ConfigError("parse job payload missing raw_document_id")
    raw_doc = session.get(RawDocument, UUID(str(raw_id)))
    if raw_doc is None:
        raise ConfigError(f"raw_document {raw_id} not found")
    source = session.get(Source, job.source_id)
    if source is None:
        raise ConfigError(f"source {job.source_id} not found")
    raw_bytes = raw_store.get(raw_doc.path)
    parser_ref = payload.get("parser_ref") or raw_doc.parser_ref
    if parser_ref:
        typed_payload = {str(key): value for key, value in payload.items()}
        records = [
            build_fetched_entry_record(
                source, raw_doc, raw_bytes, payload=typed_payload, clock=clock
            )
        ]
    else:
        records = build_document_records(
            source, raw_doc, raw_bytes, clock=clock, only_guid=payload.get("guid")
        )
    return _persist(session, records, clock=clock)


def run_one_parse(
    engine: Engine,
    *,
    raw_store: RawStore,
    clock: Clock | None = None,
    worker_id: str = "parse-worker",
) -> ParseJobResult | None:
    """Claim and process one parse job. Returns ``None`` if the queue is empty.

    Broadly catches at this service boundary so one bad job fails (retry/dead) rather than
    crashing the worker loop (CLAUDE.md §18).
    """
    clock = clock or SystemClock()
    with Session(engine) as session, session.begin():
        job = claim_next(session, worker_id=worker_id, now=clock.now(), job_types=[JobType.PARSE])
        job_id = job.id if job is not None else None
    if job_id is None:
        return None

    with Session(engine) as session:
        try:
            with session.begin():
                job = session.get(CrawlJob, job_id)
                assert job is not None  # noqa: S101 — just claimed in the prior txn
                result = run_parse_job(session, job, raw_store=raw_store, clock=clock)
                complete(session, job, now=clock.now())
            return result
        except Exception as exc:  # service boundary: fail the job, keep the worker alive
            with session.begin():
                job = session.get(CrawlJob, job_id)
                if job is not None:
                    fail(session, job, now=clock.now(), error=f"{type(exc).__name__}: {exc}")
            _log.warning("parse_job_failed", job_id=str(job_id), error=str(exc))
            return ParseJobResult(0, 0, 0, error=str(exc))


def reparse_raw_document(
    engine: Engine,
    raw_document_id: UUID,
    *,
    raw_store: RawStore,
    clock: Clock | None = None,
) -> ParseJobResult:
    """Re-parse every item in one immutable raw document (the ``atlas parse <raw_id>`` path).

    Reproducible by definition: the pinned ``parser_version`` runs against immutable raw,
    and the upsert is last-write-wins (R2)."""
    clock = clock or SystemClock()
    with Session(engine) as session, session.begin():
        raw_doc = session.get(RawDocument, raw_document_id)
        if raw_doc is None:
            raise ConfigError(f"raw_document {raw_document_id} not found")
        source = session.get(Source, raw_doc.source_id)
        if source is None:
            raise ConfigError(f"source {raw_doc.source_id} not found")
        raw_bytes = raw_store.get(raw_doc.path)
        if raw_doc.parser_ref:
            existing = session.scalar(
                select(Document).where(Document.raw_document_id == raw_doc.id).limit(1)
            )
            payload: dict[str, object] = {
                "guid": str(existing.id) if existing is not None else str(raw_doc.id),
                "link": existing.canonical_url if existing is not None else raw_doc.origin_url,
                "title": existing.title if existing is not None else None,
                "published_at": (
                    existing.published_at.isoformat()
                    if existing is not None and existing.published_at is not None
                    else None
                ),
                "parser_ref": raw_doc.parser_ref,
            }
            records = [
                build_fetched_entry_record(source, raw_doc, raw_bytes, payload=payload, clock=clock)
            ]
        else:
            records = build_document_records(source, raw_doc, raw_bytes, clock=clock)
        return _persist(session, records, clock=clock)
