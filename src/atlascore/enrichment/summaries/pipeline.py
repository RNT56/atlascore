"""The summary pipeline (docs/mcp §4): dedupe-before-summarize → content-hash cache → backend.

`summarize_documents` is the single entry point behind `get_document_summary`:

1. Load the ordered member documents (any missing → a `not_found` outcome, never a fabrication).
2. Compute `source_hash` over the ordered member `content_hash`es (R4).
3. **Cache.** With a backend, look up the exact `(source_hash, model, version, prompt)` tuple — a
   hit returns immediately (no compute, no spend). Without a backend (`LLM_BACKEND=none`) or with
   `allow_compute=false`, look up *any* cached summary for this content (cache-only mode).
4. **Compute on miss** (only when a backend is configured and compute is allowed): summarize the
   deduplicated members, store the row (idempotent on the cache key), and return it.

Member document bodies are **untrusted evidence** (CLAUDE.md §15): the extractive backend copies
already-sanitized text and never follows embedded instructions; a hosted backend's prompt keeps
them separated. Identical `(content, model, prompt)` tuples are never re-summarized (R4).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.settings import get_settings
from atlascore.enrichment.summaries.base import SummaryBackend, SummaryInput
from atlascore.storage.llm_spend import month_to_date_spend, period_ym, record_spend
from atlascore.storage.summaries import (
    MemberProvenance,
    SummaryView,
    compute_source_hash,
    get_any_cached_summary,
    get_cached_summary,
    insert_summary,
    load_member_documents,
    member_provenance,
    to_view,
)

CacheState = Literal["hit", "miss"]


@dataclass(frozen=True)
class SummaryOutcome:
    """The result of a summary request: a view on hit, else a reason (never a fabrication)."""

    found: bool
    cache: CacheState
    summary: SummaryView | None
    members: list[MemberProvenance]
    member_doc_ids: list[str]
    reason: str | None = None


def summarize_documents(
    session: Session,
    doc_ids: list[UUID],
    *,
    backend: SummaryBackend | None,
    allow_compute: bool = True,
    clock: Clock | None = None,
) -> SummaryOutcome:
    """Return a cached/freshly-computed cited summary for the ordered ``doc_ids``. Caller's txn.

    A **paid** backend (``is_paid``) is gated by ``LLM_MONTHLY_SPEND_CAP_EUR`` (§20): if month-to-date
    spend is at/over the cap, compute is skipped and the request degrades to cache-only (a cached row
    is still returned; otherwise a ``found=False`` reason). A successful paid compute records one
    ``llm_spend`` ledger row in this same transaction.
    """
    clock = clock or SystemClock()
    member_ids = [str(d) for d in doc_ids]
    documents = load_member_documents(session, doc_ids)
    if documents is None:
        return SummaryOutcome(
            found=False,
            cache="miss",
            summary=None,
            members=[],
            member_doc_ids=member_ids,
            reason="one or more document_ids do not exist",
        )

    source_hash = compute_source_hash([d.content_hash for d in documents])
    members = member_provenance(session, doc_ids)

    compute = allow_compute and backend is not None
    # §20 hard spend cap: a paid backend at/over the month-to-date cap degrades to cache-only.
    over_cap = False
    if compute and getattr(backend, "is_paid", False):
        cap = Decimal(str(get_settings().llm_monthly_spend_cap_eur))
        if month_to_date_spend(session, period_ym(clock.now())) >= cap:
            compute, over_cap = False, True
    if compute:
        assert backend is not None  # noqa: S101 — guarded by `compute`
        cached = get_cached_summary(
            session,
            source_hash=source_hash,
            model_name=backend.model_name,
            model_version=backend.model_version,
            prompt_version=backend.prompt_version,
        )
    else:
        cached = get_any_cached_summary(session, source_hash=source_hash)

    if cached is not None:
        return SummaryOutcome(
            found=True,
            cache="hit",
            summary=to_view(cached),
            members=members,
            member_doc_ids=member_ids,
        )

    if not compute:
        if over_cap:
            reason = "LLM monthly spend cap reached; cache-only"
        elif backend is not None:
            reason = "allow_compute=false and no cached summary for this content"
        else:
            reason = (
                "LLM_BACKEND=none and no cached summary for this (content, model, prompt) tuple"
            )
        return SummaryOutcome(
            found=False,
            cache="miss",
            summary=None,
            members=members,
            member_doc_ids=member_ids,
            reason=reason,
        )

    assert backend is not None  # noqa: S101 — guarded by `compute`
    result = backend.summarize(
        [
            SummaryInput(document_id=str(d.id), title=d.title, body_text=d.body_text)
            for d in documents
        ]
    )
    stored = insert_summary(
        session,
        doc_ids=doc_ids,
        source_hash=source_hash,
        model_name=backend.model_name,
        model_version=backend.model_version,
        prompt_version=backend.prompt_version,
        summary_text=result.summary_text,
        citations=result.citations,
        token_cost=result.token_cost,
    )
    # §20: record the paid call's cost in the same txn so the cap is enforced cumulatively.
    if getattr(backend, "is_paid", False):
        record_spend(
            session,
            provider=getattr(backend, "provider", backend.model_name),
            model=backend.model_version,
            request_kind="summary",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            now=clock.now(),
            summary_id=stored.id if hasattr(stored, "id") else None,
        )
    return SummaryOutcome(
        found=True,
        cache="miss",
        summary=to_view(stored),
        members=members,
        member_doc_ids=member_ids,
    )


__all__ = ["CacheState", "SummaryOutcome", "summarize_documents"]
