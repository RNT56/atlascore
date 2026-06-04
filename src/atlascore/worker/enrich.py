"""The enrich worker (CLAUDE.md §6 flow): consume ``enrich`` jobs → run the enrichment pipeline.

The parse worker enqueues one ``enrich`` job per changed ``document`` (payload ``{document_id}``,
idempotent dedupe_key). Like parse, enrichment does **no source I/O** (it reads only the DB and the
chosen backends), so it scales out via ``SELECT … FOR UPDATE SKIP LOCKED`` and needs no fetch lock.
Claim and process run in SEPARATE transactions so a crash can't lose the claim and ``fail()`` always
runs in a clean transaction (CLAUDE.md §18).

``atlas enrich <doc_id>`` calls :func:`enrich_document_by_id` directly (no queue) for a one-shot /
re-enrich. The default backends are the deterministic MVP ones (rule NER + hashing embeddings).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import ConfigError
from atlascore.core.logging import get_logger
from atlascore.enrichment.embeddings import EmbeddingBackend
from atlascore.enrichment.ner import NerBackend
from atlascore.enrichment.pipeline import EnrichmentResult, default_backends, enrich_document
from atlascore.schemas.crawl_job import JobType
from atlascore.storage.postgres.models import CrawlJob, Document
from atlascore.storage.queue import claim_next, complete, fail

_log = get_logger("atlascore.worker.enrich")


def run_enrich_job(
    session: Session,
    job: CrawlJob,
    *,
    ner: NerBackend,
    embedder: EmbeddingBackend,
) -> EnrichmentResult:
    """Run one enrich job: load the document by id → run the enrichment pipeline."""
    payload = job.payload or {}
    doc_id = payload.get("document_id")
    if not doc_id:
        raise ConfigError("enrich job payload missing document_id")
    document = session.get(Document, UUID(str(doc_id)))
    if document is None:
        raise ConfigError(f"document {doc_id} not found")
    return enrich_document(session, document, ner=ner, embedder=embedder)


def run_one_enrich(
    engine: Engine,
    *,
    ner: NerBackend | None = None,
    embedder: EmbeddingBackend | None = None,
    clock: Clock | None = None,
    worker_id: str = "enrich-worker",
) -> EnrichmentResult | None:
    """Claim and process one enrich job. Returns ``None`` if the queue is empty.

    Broadly catches at this service boundary so one bad job fails (retry/dead) rather than
    crashing the worker loop (CLAUDE.md §18).
    """
    clock = clock or SystemClock()
    if ner is None or embedder is None:
        default_ner, default_embedder = default_backends()
        ner = ner or default_ner
        embedder = embedder or default_embedder

    with Session(engine) as session, session.begin():
        job = claim_next(session, worker_id=worker_id, now=clock.now(), job_types=[JobType.ENRICH])
        job_id = job.id if job is not None else None
    if job_id is None:
        return None

    with Session(engine) as session:
        try:
            with session.begin():
                job = session.get(CrawlJob, job_id)
                assert job is not None  # noqa: S101 — just claimed in the prior txn
                result = run_enrich_job(session, job, ner=ner, embedder=embedder)
                complete(session, job, now=clock.now())
            return result
        except Exception as exc:  # service boundary: fail the job, keep the worker alive
            with session.begin():
                job = session.get(CrawlJob, job_id)
                if job is not None:
                    fail(session, job, now=clock.now(), error=f"{type(exc).__name__}: {exc}")
            _log.warning("enrich_job_failed", job_id=str(job_id), error=str(exc))
            return EnrichmentResult(str(job_id), None, [], 0, 0, 0)


def enrich_document_by_id(
    engine: Engine,
    document_id: UUID,
    *,
    ner: NerBackend | None = None,
    embedder: EmbeddingBackend | None = None,
) -> EnrichmentResult:
    """Enrich one document directly (the ``atlas enrich <doc_id>`` path; no queue)."""
    if ner is None or embedder is None:
        default_ner, default_embedder = default_backends()
        ner = ner or default_ner
        embedder = embedder or default_embedder
    with Session(engine) as session, session.begin():
        document = session.get(Document, document_id)
        if document is None:
            raise ConfigError(f"document {document_id} not found")
        return enrich_document(session, document, ner=ner, embedder=embedder)
