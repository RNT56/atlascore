"""Full-text search over the canonical ``document`` table (B6).

The query reuses the ``ix_document_fts`` GIN expression index created in migration ``0003``
(``gin(to_tsvector('simple', coalesce(body_text, '')))``): the ``WHERE`` clause uses the
*identical* tsvector expression so the planner can use the index. The user's free-text query
is parsed with ``websearch_to_tsquery('simple', …)`` — robust against arbitrary input (never
raises on stray operators; supports ``"quoted phrases"``, ``or``, and ``-term`` exclusion).
Results are ranked by ``ts_rank`` and carry a contextual ``ts_headline`` snippet plus
provenance (source, canonical URL, published time).

The ``'simple'`` text-search config (no stemming / stopwords) is used on **both** sides so
query terms match exactly what the index stored; it must stay in lockstep with the index —
changing it here without reindexing would silently stop using ``ix_document_fts`` and change
match semantics.

The SQL is fully static (no string interpolation): every caller value — including the
optional filters, which are guarded with ``CAST(:p AS …) IS NULL OR …`` — is a bound
parameter, so there is no injection surface. Only sanitized ``body_text`` is searched and
returned (CLAUDE.md §13); raw HTML is never exposed by this path. The result count is capped
(``MAX_LIMIT``) to bound MCP/CLI payloads (CLAUDE.md §15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# Result caps (CLAUDE.md §15: cap result sizes). The CLI default is DEFAULT_LIMIT; a caller
# asking for more than MAX_LIMIT is clamped, never unbounded.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# One short ts_headline fragment with the matched terms wrapped in guillemets the CLI can
# render. Non-empty markers are required — Postgres mishandles empty StartSel/StopSel.
_SEARCH_SQL = text(
    """
    SELECT d.id                                          AS document_id,
           s.config->>'slug'                             AS source_slug,
           s.name                                        AS source_name,
           d.canonical_url                               AS canonical_url,
           d.title                                       AS title,
           d.published_at                                AS published_at,
           ts_rank(to_tsvector('simple', coalesce(d.body_text, '')),
                   websearch_to_tsquery('simple', :q))   AS rank,
           ts_headline('simple', coalesce(d.body_text, ''),
                       websearch_to_tsquery('simple', :q),
                       'MaxFragments=1, MaxWords=24, MinWords=8, StartSel=«, StopSel=»') AS snippet
    FROM document d
    JOIN source s ON s.id = d.source_id
    WHERE to_tsvector('simple', coalesce(d.body_text, ''))
          @@ websearch_to_tsquery('simple', :q)
      AND (CAST(:source AS text) IS NULL OR s.config->>'slug' = :source)
      AND (CAST(:since AS timestamptz) IS NULL OR d.published_at >= :since)
      AND (CAST(:until AS timestamptz) IS NULL OR d.published_at < :until)
    ORDER BY rank DESC, d.published_at DESC NULLS LAST
    LIMIT :limit
    """
)


@dataclass(frozen=True)
class SearchHit:
    """One ranked full-text match with provenance (no raw HTML; sanitized snippet only)."""

    document_id: UUID
    source_slug: str | None
    source_name: str
    canonical_url: str | None
    title: str | None
    published_at: datetime | None
    rank: float
    snippet: str


def search_documents(
    session: Session,
    query: str,
    *,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Rank ``document`` rows by full-text match on ``body_text`` with optional filters.

    ``source`` filters by the source slug (``config->>'slug'``); ``since``/``until`` bound
    ``published_at`` (``since`` inclusive, ``until`` exclusive). An empty/whitespace query
    returns ``[]`` (no match attempted). ``limit`` is clamped to ``[1, MAX_LIMIT]``. The
    caller manages the transaction/session.
    """
    cleaned = query.strip()
    if not cleaned:
        return []
    capped = max(1, min(limit, MAX_LIMIT))

    rows = session.execute(
        _SEARCH_SQL,
        {"q": cleaned, "source": source, "since": since, "until": until, "limit": capped},
    ).mappings()
    return [
        SearchHit(
            document_id=row["document_id"],
            source_slug=row["source_slug"],
            source_name=row["source_name"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            published_at=row["published_at"],
            rank=float(row["rank"]),
            snippet=row["snippet"] or "",
        )
        for row in rows
    ]
