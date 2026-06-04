"""The fetch worker (docs/architecture, docs/connectors §3, §5).

Drives one `fetch` crawl_job through the connector: fetch (SSRF-guarded, conditional) → store
raw before parse → parse entries → dedupe by enqueuing one `parse` job per GUID (idempotent, so
re-fetching ignores already-seen GUIDs) → checkpoint via the stored ETag/Last-Modified.

Claim and process run in SEPARATE transactions so a process failure can't lose the claim, and
`fail()` (retry/dead) always runs in a clean transaction. The single-fetch-worker startup guard
(R16) is a Postgres advisory lock — a second fetch worker is refused until the documented
`host_rate_state` scale-up path exists.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import ConfigError, FetchWorkerBusyError, PolicyError
from atlascore.core.logging import get_logger
from atlascore.schemas import SCHEMA_VERSION
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import PolicyStatus
from atlascore.storage.postgres.models import CrawlJob, RawDocument, Source
from atlascore.storage.queue import claim_next, complete, enqueue, fail
from atlascore.storage.rawstore import RawStore

_log = get_logger("atlascore.worker")

# Advisory-lock key for the single fetch role ("ATLSFTCH").
FETCH_LOCK_KEY = 0x41544C53_46544348


@dataclass(frozen=True)
class FetchJobResult:
    not_modified: bool
    new_entries: int
    total_entries: int
    raw_document_id: UUID | None
    error: str | None = None


def load_connector(ref: str) -> Connector:
    """Import a connector from a ``module.path:ClassName`` ref and instantiate it."""
    module_path, _, class_name = ref.partition(":")
    if not module_path or not class_name:
        raise ConfigError(f"invalid connector ref {ref!r} (expected 'module.path:ClassName')")
    cls = getattr(importlib.import_module(module_path), class_name)
    return cast("Connector", cls())


def fetch_worker_active(session: Session) -> bool:
    """True if some process currently holds the fetch advisory lock (a fetch worker is running).

    Inspects ``pg_locks`` without acquiring the lock. A 64-bit advisory key is split across
    ``classid`` (high 32 bits) and ``objid`` (low 32 bits) with ``objsubid=1`` (the
    ``pg_try_advisory_lock(bigint)`` form). Read-only liveness for `atlas health` / `atlas digest`.
    """
    held = session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = :hi AND objid = :lo AND objsubid = 1"
        ),
        {"hi": FETCH_LOCK_KEY >> 32, "lo": FETCH_LOCK_KEY & 0xFFFFFFFF},
    )
    return bool(held)


@contextmanager
def fetch_worker_lock(engine: Engine) -> Iterator[None]:
    """Single-fetch-worker guard (R16): a pg advisory lock held for the fetch operation.

    Raises :class:`FetchWorkerBusyError` if another fetch worker already holds it.
    """
    conn = engine.connect()
    try:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": FETCH_LOCK_KEY}
        ).scalar()
        if not acquired:
            raise FetchWorkerBusyError(
                "another fetch worker holds the lock (single-fetch-worker invariant, R16)"
            )
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": FETCH_LOCK_KEY})
            conn.commit()
    finally:
        conn.close()


def run_fetch_job(
    session: Session,
    job: CrawlJob,
    *,
    fetcher: SafeFetcher,
    raw_store: RawStore,
    clock: Clock,
) -> FetchJobResult:
    """Run one fetch job: fetch → store raw → parse → dedupe-enqueue. Caller manages the txn."""
    source = session.get(Source, job.source_id)
    if source is None:
        raise ConfigError(f"source {job.source_id} not found")
    status = PolicyStatus(source.policy_status)
    if not (source.enabled and status.is_fetchable):
        raise PolicyError(
            f"refusing to fetch {source.name}: policy={status.value}, enabled={source.enabled}"
        )
    config = source.config or {}
    connector_ref = config.get("connector")
    if not connector_ref:
        raise ConfigError(f"source {source.name} has no fetch.connector configured")
    if not source.base_url:
        raise ConfigError(f"source {source.name} has no base_url")
    connector = load_connector(connector_ref)

    last_raw = session.scalar(
        select(RawDocument)
        .where(RawDocument.source_id == source.id)
        .order_by(RawDocument.fetched_at.desc())
        .limit(1)
    )
    rate_limit = source.rate_limit or {}
    ctx = FetchContext(
        feed_url=source.base_url,
        etag=last_raw.etag if last_raw else None,
        last_modified=last_raw.last_modified if last_raw else None,
        timeout=float(config.get("timeout_seconds", 30)),
        max_requests=int(rate_limit.get("max_requests", 1)),
        window_seconds=float(rate_limit.get("window_seconds", 1)),
    )

    outcome = connector.fetch(fetcher, ctx)
    if outcome.not_modified:
        return FetchJobResult(True, 0, 0, None)

    blob = raw_store.put(outcome.content)
    raw_doc = session.scalar(
        select(RawDocument).where(RawDocument.content_hash == blob.content_hash)
    )
    if raw_doc is None:
        raw_doc = RawDocument(
            source_id=source.id,
            crawl_job_id=job.id,
            fetched_at=clock.now(),
            content_hash=blob.content_hash,
            mime_type=outcome.mime_type,
            path=blob.relpath,
            size_bytes=blob.size_bytes,
            http_status=outcome.status,
            etag=outcome.etag,
            last_modified=outcome.last_modified,
            schema_version=SCHEMA_VERSION,
        )
        session.add(raw_doc)
        session.flush()

    entries = connector.parse_entries(outcome.content)
    slug = config.get("slug") or str(source.id)
    new_entries = 0
    for entry in entries:
        # Key the parse job on (guid, raw content hash), not the guid alone: when a publisher
        # edits an item in place under a stable GUID, the feed's bytes change -> a NEW raw is
        # stored -> a NEW parse job is enqueued and the edit is re-parsed (last-write-wins, R2).
        # An unchanged refetch yields the same raw hash -> same key -> idempotent no-op.
        dedupe_key = f"parse:{slug}:{entry.guid}:{raw_doc.content_hash}"
        already = session.scalar(select(CrawlJob.id).where(CrawlJob.dedupe_key == dedupe_key))
        enqueue(
            session,
            source_id=source.id,
            job_type=JobType.PARSE,
            dedupe_key=dedupe_key,
            scheduled_at=clock.now(),
            payload={
                "guid": entry.guid,
                "link": entry.link,
                "title": entry.title,
                "raw_document_id": str(raw_doc.id),
            },
        )
        if already is None:
            new_entries += 1
    return FetchJobResult(False, new_entries, len(entries), raw_doc.id)


def run_one_fetch(
    engine: Engine,
    *,
    fetcher: SafeFetcher,
    raw_store: RawStore,
    clock: Clock | None = None,
    worker_id: str = "fetch-worker",
) -> FetchJobResult | None:
    """Claim and process one fetch job. Returns ``None`` if the queue is empty.

    Broadly catches at this service boundary so a single bad job fails (retry/dead) rather than
    crashing the worker loop (CLAUDE.md §18).
    """
    clock = clock or SystemClock()
    with Session(engine) as session, session.begin():
        job = claim_next(session, worker_id=worker_id, now=clock.now(), job_types=[JobType.FETCH])
        job_id = job.id if job is not None else None
    if job_id is None:
        return None

    with Session(engine) as session:
        try:
            with session.begin():
                job = session.get(CrawlJob, job_id)
                assert job is not None  # noqa: S101 — just claimed in the prior txn
                result = run_fetch_job(
                    session, job, fetcher=fetcher, raw_store=raw_store, clock=clock
                )
                complete(session, job, now=clock.now())
            return result
        except Exception as exc:  # service boundary: fail the job, keep the worker alive
            with session.begin():
                job = session.get(CrawlJob, job_id)
                if job is not None:
                    fail(session, job, now=clock.now(), error=f"{type(exc).__name__}: {exc}")
            _log.warning("fetch_job_failed", job_id=str(job_id), error=str(exc))
            return FetchJobResult(False, 0, 0, None, error=str(exc))
