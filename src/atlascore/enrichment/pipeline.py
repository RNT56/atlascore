"""The enrichment pipeline (CLAUDE.md §6 flow, docs/schemas §2.4/§2.6/§2.8/§2.9).

Turns a canonical ``document`` into the enrichment surface, in order: **language → NER → topics →
embeddings**. It writes ``document.language``/``topics``/``entity_ids`` in place, resolves each NER
candidate to a canonical ``entity`` (find-or-create), records a sanitized ``mention`` per entity,
and embeds the body into ``embedding`` chunk vectors. All of this runs in the caller's transaction
(one canonical multi-table write).

**Idempotent (B11 / R13).** Re-enriching a document first clears that document's mentions from this
NER model+version, then re-creates them, and the embedding upsert is a no-op when the body's
``content_hash`` is unchanged — so replays and re-parses converge without duplication. Entities are
never deleted by enrichment (they may be referenced by other documents); only the purge path
removes them.

External text is **untrusted evidence** (CLAUDE.md §15): ``context_snippet`` is taken from the
already-sanitized ``document.body_text`` and is data, never instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from atlascore.enrichment.embeddings import EmbeddingBackend, get_embedding_backend
from atlascore.enrichment.language import detect_language
from atlascore.enrichment.ner import NerBackend, get_ner_backend
from atlascore.enrichment.topics import extract_topics
from atlascore.storage.embeddings import upsert_embeddings
from atlascore.storage.entities import find_or_create_entity, upsert_mention
from atlascore.storage.postgres.models import Document, Mention

# Sanitized context window around a mention (docs/mcp §2.3: snippets capped ~320 chars).
CONTEXT_SNIPPET_CHARS = 320


@dataclass(frozen=True)
class EnrichmentResult:
    document_id: str
    language: str | None
    topics: list[str]
    entities: int
    mentions: int
    embeddings: int


def _context_snippet(body: str, surface: str) -> str | None:
    """A ~320-char window of (already-sanitized) body text around the first mention occurrence."""
    if not body:
        return None
    idx = body.lower().find(surface.lower())
    if idx < 0:
        return body[:CONTEXT_SNIPPET_CHARS].strip() or None
    half = CONTEXT_SNIPPET_CHARS // 2
    start = max(0, idx - half)
    end = min(len(body), idx + len(surface) + half)
    snippet = body[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(body):
        snippet = snippet + "…"
    return snippet or None


def enrich_document(
    session: Session,
    document: Document,
    *,
    ner: NerBackend,
    embedder: EmbeddingBackend,
) -> EnrichmentResult:
    """Run language → NER → topics → embeddings for one document. Caller manages the txn."""
    body = document.body_text or ""
    text = f"{document.title or ''}\n{body}".strip()

    document.language = detect_language(body, fallback=document.language)
    document.topics = extract_topics(body, title=document.title)

    # Idempotent NER: clear this document's mentions from this model+version, then re-create.
    session.execute(
        delete(Mention).where(
            Mention.document_id == document.id,
            Mention.model_name == ner.model_name,
            Mention.model_version == ner.model_version,
        )
    )
    candidates = ner.extract(text)
    entity_ids: list[str] = []
    mentions = 0
    for candidate in candidates:
        entity = find_or_create_entity(session, candidate)
        upsert_mention(
            session,
            entity_id=entity.id,
            document_id=document.id,
            mentioned_at=document.published_at,
            confidence=candidate.confidence,
            context_snippet=_context_snippet(body, candidate.text),
            model_name=ner.model_name,
            model_version=ner.model_version,
        )
        mentions += 1
        if str(entity.id) not in entity_ids:
            entity_ids.append(str(entity.id))
    document.entity_ids = entity_ids

    embeddings = upsert_embeddings(
        session,
        document_id=document.id,
        source_hash=document.content_hash,
        body_text=body,
        backend=embedder,
    )
    session.flush()
    return EnrichmentResult(
        document_id=str(document.id),
        language=document.language,
        topics=list(document.topics),
        entities=len(entity_ids),
        mentions=mentions,
        embeddings=embeddings,
    )


def default_backends() -> tuple[NerBackend, EmbeddingBackend]:
    """Construct the configured NER + embedding backends (the worker/CLI default)."""
    return get_ner_backend(), get_embedding_backend()


__all__ = ["EnrichmentResult", "default_backends", "enrich_document"]
