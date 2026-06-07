"""Macro (time-series) fetch processing (B9): fetch a series → store raw → upsert observations.

Runs inside the single fetch worker (``run_one_fetch`` dispatches here when a claimed fetch job
belongs to an ``access_method=api`` source). Like the RSS path it captures the immutable raw
payload **before** parsing; unlike RSS it normalizes the observations inline into
``time_series_point`` (a series is small and has no per-item fan-out).

Secret handling (§14): the FRED api key is a query parameter, so any error string is routed
through ``sanitize_for_storage`` before it is logged — the URL query (and thus the key) is
stripped. ``crawl_job.last_error`` is sanitized by the queue's ``fail()`` already.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk.base import FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.core.clock import Clock
from atlascore.core.errors import ConfigError, PolicyError
from atlascore.schemas import SCHEMA_VERSION
from atlascore.schemas.source import PolicyStatus
from atlascore.security.secrets import resolve_credential
from atlascore.storage.postgres.models import CrawlJob, RawDocument, Source
from atlascore.storage.rawstore import RawStore
from atlascore.storage.timeseries import upsert_observations
from atlascore.worker.run import load_connector


@dataclass(frozen=True)
class MacroFetchResult:
    not_modified: bool
    points_written: int
    series_id: str | None
    raw_document_id: UUID | None
    error: str | None = None


def run_macro_fetch_job(
    session: Session,
    job: CrawlJob,
    *,
    fetcher: SafeFetcher,
    raw_store: RawStore,
    clock: Clock,
) -> MacroFetchResult:
    """Run one macro fetch job: fetch the series → store raw → upsert observations."""
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
    params: dict[str, str] = config.get("params") or {}
    series_id = params.get("series_id")
    if not series_id:
        raise ConfigError(f"source {source.name} has no fetch.params.series_id")

    connector = load_connector(connector_ref)
    rate_limit = source.rate_limit or {}
    ctx = FetchContext(
        feed_url=source.base_url,
        timeout=float(config.get("timeout_seconds", 30)),
        max_requests=int(rate_limit.get("max_requests", 1)),
        window_seconds=float(rate_limit.get("window_seconds", 1)),
        params=params,
        credential=resolve_credential(source.credential_ref),
    )

    outcome = connector.fetch(fetcher, ctx)
    if outcome.not_modified:
        return MacroFetchResult(True, 0, series_id, None)

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

    series = connector.parse_observations(outcome.content, series_id=series_id)
    unit = params.get("unit")
    # Operator-provided human unit label (FRED's observations endpoint omits it). Only applied when
    # the connector did NOT already derive a unit from the payload — otherwise a connector whose
    # `unit` param is a real dimension FILTER (Eurostat `unit=RCH_A`) would clobber the parsed label.
    if unit and series.unit is None:
        series = replace(series, unit=unit)
    points = upsert_observations(session, series)
    return MacroFetchResult(False, points, series_id, raw_doc.id)
