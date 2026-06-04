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
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from atlascore.enrichment.summaries.base import SummaryBackend, SummaryInput
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
) -> SummaryOutcome:
    """Return a cached/freshly-computed cited summary for the ordered ``doc_ids``. Caller's txn."""
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
        reason = (
            "allow_compute=false and no cached summary for this content"
            if backend is not None
            else "LLM_BACKEND=none and no cached summary for this (content, model, prompt) tuple"
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
    return SummaryOutcome(
        found=True,
        cache="miss",
        summary=to_view(stored),
        members=members,
        member_doc_ids=member_ids,
    )


__all__ = ["CacheState", "SummaryOutcome", "summarize_documents"]
