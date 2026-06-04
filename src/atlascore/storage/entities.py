"""``entity`` + ``mention`` persistence and resolution (docs/schemas §2.8/§2.9, docs/mcp §2.3).

Two sides:

- **Write (enrichment):** :func:`find_or_create_entity` resolves a NER candidate to a canonical
  ``entity`` (by identifier, then exact normalized name/alias) or creates one; :func:`upsert_mention`
  writes one mention per ``(entity, document, ner-model-version)`` idempotently (R13-style), so
  re-enriching the same document never duplicates mentions.
- **Read (``get_entity_mentions``):** :func:`resolve_entity` looks an entity up by exact id, then
  identifier (jsonb containment), then **alias-aware trigram fuzzy** name match (``pg_trgm``),
  resolving ``merged_into`` to the surviving id; :func:`list_mentions` returns the capped,
  time-ordered mentions joined to their document/source provenance. Context snippets are sanitized
  upstream (evidence, not instructions).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlascore.enrichment.ner import EntityCandidate, normalize_name
from atlascore.schemas import SCHEMA_VERSION
from atlascore.storage.postgres.models import Document, Entity, Mention, Source

# Minimum trigram similarity for a fuzzy name match (pg_trgm default threshold is 0.3).
NAME_MATCH_THRESHOLD = 0.3
# merged_into chain depth guard (a cycle should never happen, but never loop forever).
_MERGE_CHAIN_MAX = 16


# --------------------------------------------------------------------------- write side


def find_or_create_entity(session: Session, candidate: EntityCandidate) -> Entity:
    """Resolve a NER candidate to an ``entity`` (identifier → exact name/alias) or create one.

    Caller manages the transaction. New identifiers/aliases discovered on an existing entity are
    merged in so the canonical row accretes structure over time.
    """
    existing: Entity | None = None
    if candidate.identifiers:
        existing = session.scalar(
            select(Entity).where(Entity.identifiers.contains(candidate.identifiers)).limit(1)
        )
    if existing is None:
        existing = session.scalar(
            select(Entity)
            .where(
                (func.lower(Entity.canonical_name) == candidate.normalized_name)
                | Entity.aliases.contains([candidate.normalized_name])
            )
            .limit(1)
        )

    if existing is not None:
        _accrete(existing, candidate)
        return existing

    entity = Entity(
        entity_type=candidate.entity_type,
        canonical_name=candidate.text,
        aliases=[candidate.normalized_name],
        identifiers=dict(candidate.identifiers),
        confidence=Decimal(str(candidate.confidence)),
        schema_version=SCHEMA_VERSION,
    )
    session.add(entity)
    session.flush()
    return entity


def _accrete(entity: Entity, candidate: EntityCandidate) -> None:
    """Fold a candidate's new identifiers/aliases into an existing entity (in place)."""
    if candidate.identifiers:
        merged = {**entity.identifiers, **candidate.identifiers}
        if merged != entity.identifiers:
            entity.identifiers = merged
    if candidate.normalized_name not in entity.aliases:
        entity.aliases = [*entity.aliases, candidate.normalized_name]


def upsert_mention(
    session: Session,
    *,
    entity_id: UUID,
    document_id: UUID,
    mentioned_at: datetime | None,
    confidence: float,
    context_snippet: str | None,
    model_name: str,
    model_version: str,
    sentiment: float | None = None,
) -> None:
    """Idempotently write one mention (unique on entity+document+ner-model-version)."""
    stmt = insert(Mention).values(
        entity_id=entity_id,
        document_id=document_id,
        mentioned_at=mentioned_at,
        confidence=Decimal(str(confidence)),
        sentiment=None if sentiment is None else Decimal(str(sentiment)),
        context_snippet=context_snippet,
        model_name=model_name,
        model_version=model_version,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["entity_id", "document_id", "model_name", "model_version"],
        set_={
            "mentioned_at": stmt.excluded.mentioned_at,
            "confidence": stmt.excluded.confidence,
            "sentiment": stmt.excluded.sentiment,
            "context_snippet": stmt.excluded.context_snippet,
        },
    )
    session.execute(stmt)


# --------------------------------------------------------------------------- read side


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: UUID
    entity_type: str
    canonical_name: str
    aliases: list[str]
    identifiers: dict[str, str]
    merged_into: UUID | None
    match_confidence: float


@dataclass(frozen=True)
class MentionRow:
    mention_id: UUID
    document_id: UUID
    mentioned_at: datetime | None
    confidence: float | None
    sentiment: float | None
    context_snippet: str | None
    model_name: str
    model_version: str
    source_name: str
    source_id: UUID
    canonical_url: str | None
    published_at: datetime | None


def _surviving(session: Session, entity: Entity) -> Entity:
    """Follow ``merged_into`` to the surviving entity (cycle/depth guarded)."""
    seen: set[UUID] = set()
    current = entity
    for _ in range(_MERGE_CHAIN_MAX):
        if current.merged_into is None or current.merged_into in seen:
            return current
        seen.add(current.id)
        nxt = session.get(Entity, current.merged_into)
        if nxt is None:
            return current
        current = nxt
    return current


def _to_resolved(entity: Entity, match_confidence: float) -> ResolvedEntity:
    return ResolvedEntity(
        entity_id=entity.id,
        entity_type=entity.entity_type,
        canonical_name=entity.canonical_name,
        aliases=list(entity.aliases or []),
        identifiers=dict(entity.identifiers or {}),
        merged_into=entity.merged_into,
        match_confidence=match_confidence,
    )


def resolve_entity(
    session: Session,
    *,
    entity_id: UUID | None = None,
    name: str | None = None,
    identifier: dict[str, str] | None = None,
) -> ResolvedEntity | None:
    """Resolve an entity by exact id, then identifier, then alias-aware fuzzy name.

    Returns the **surviving** entity (``merged_into`` followed) with a ``match_confidence``
    (1.0 exact id, 0.95 identifier, trigram similarity for a fuzzy name), or ``None`` if no match.
    """
    if entity_id is not None:
        entity = session.get(Entity, entity_id)
        return _to_resolved(_surviving(session, entity), 1.0) if entity is not None else None

    if identifier:
        entity = session.scalar(
            select(Entity).where(Entity.identifiers.contains(identifier)).limit(1)
        )
        if entity is not None:
            return _to_resolved(_surviving(session, entity), 0.95)

    if name:
        norm = normalize_name(name)
        # Exact normalized name / alias first (confidence 1.0), else trigram fuzzy.
        exact = session.scalar(
            select(Entity)
            .where((func.lower(Entity.canonical_name) == norm) | Entity.aliases.contains([norm]))
            .limit(1)
        )
        if exact is not None:
            return _to_resolved(_surviving(session, exact), 1.0)
        sim = func.similarity(Entity.canonical_name, name)
        row = session.execute(
            select(Entity, sim.label("sim"))
            .where(sim > NAME_MATCH_THRESHOLD)
            .order_by(sim.desc())
            .limit(1)
        ).first()
        if row is not None:
            entity, score = row
            return _to_resolved(_surviving(session, entity), float(score))

    return None


def list_mentions(
    session: Session,
    entity_id: UUID,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    min_confidence: float = 0.0,
    limit: int = 50,
) -> list[MentionRow]:
    """Return capped, time-ordered mentions of an entity, joined to document/source provenance."""
    stmt = (
        select(Mention, Document, Source)
        .join(Document, Document.id == Mention.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(Mention.entity_id == entity_id)
    )
    if since is not None:
        stmt = stmt.where(Mention.mentioned_at >= since)
    if until is not None:
        stmt = stmt.where(Mention.mentioned_at < until)
    if min_confidence > 0.0:
        stmt = stmt.where(Mention.confidence >= Decimal(str(min_confidence)))
    stmt = stmt.order_by(Mention.mentioned_at.desc().nulls_last(), Mention.id.desc()).limit(limit)

    rows: list[MentionRow] = []
    for mention, document, source in session.execute(stmt):
        rows.append(
            MentionRow(
                mention_id=mention.id,
                document_id=document.id,
                mentioned_at=mention.mentioned_at,
                confidence=float(mention.confidence) if mention.confidence is not None else None,
                sentiment=float(mention.sentiment) if mention.sentiment is not None else None,
                context_snippet=mention.context_snippet,
                model_name=mention.model_name,
                model_version=mention.model_version,
                source_name=source.name,
                source_id=source.id,
                canonical_url=document.canonical_url,
                published_at=document.published_at,
            )
        )
    return rows
