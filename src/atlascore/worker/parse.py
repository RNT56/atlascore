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

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import ConfigError
from atlascore.core.hashing import content_hash
from atlascore.core.logging import get_logger
from atlascore.parsers.article import ArticleParser
from atlascore.schemas import SCHEMA_VERSION
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.document import DocumentRecord
from atlascore.schemas.source import AccessMethod
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import CrawlJob, RawDocument, Source
from atlascore.storage.queue import claim_next, complete, fail
from atlascore.storage.rawstore import RawStore
from atlascore.worker.run import load_connector

_log = get_logger("atlascore.worker.parse")


@dataclass(frozen=True)
class ParseJobResult:
    documents_created: int
    documents_updated: int
    documents_unchanged: int
    error: str | None = None


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
    yields every item (the ``atlas parse <raw_id>`` re-parse path). RSS only in B5; the
    filing (B10) and macro (B9) parsers land with their connectors.
    """
    method = AccessMethod(source.access_method)
    if method != AccessMethod.RSS:
        raise NotImplementedError(
            f"no document parser for access_method '{method.value}' yet "
            "(B5 ships RSS; filing=B10, macro=B9)"
        )
    connector_ref = (source.config or {}).get("connector")
    if not connector_ref:
        raise ConfigError(f"source {source.name} has no fetch.connector configured")
    connector = load_connector(connector_ref)

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
        records = build_document_records(source, raw_doc, raw_bytes, clock=clock)
        return _persist(session, records, clock=clock)
