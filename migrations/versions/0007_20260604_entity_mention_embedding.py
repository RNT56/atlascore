"""entity + mention + embedding (enrichment tables, B11)

Creates the three enrichment tables (docs/schemas/README.md §2.6, §2.8, §2.9) via raw SQL —
Alembic autogenerate cannot round-trip the trigram ``gin (canonical_name gin_trgm_ops)`` index,
the array GINs, or the ``pgvector vector(N)``/HNSW index. ``pg_trgm`` and ``vector`` are already
installed by migration ``0001``. All three tables (and every object named after them) are excluded
from the autogenerate drift-check by ``_include_object`` in ``migrations/env.py``.

``embedding.vector`` is fixed at ``vector(384)`` — the deployment's ``EMBEDDING_DIM`` (R8: one
active model+dim per deployment; matches both the deterministic MVP backend and the future
``all-MiniLM-L6-v2`` swap). Changing the dim is a bounded backfill+reindex (runbook), not an online
change. FKs are ``ON DELETE RESTRICT`` (R17) so the purge job drives deletion ordering.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- entity (canonical entities + aliases + identifiers + merge history) ---
    op.execute(
        """
        CREATE TABLE entity (
            id             uuid PRIMARY KEY,
            entity_type    text NOT NULL,
            canonical_name text NOT NULL,
            aliases        text[] NOT NULL DEFAULT '{}',
            identifiers    jsonb NOT NULL DEFAULT '{}'::jsonb,
            confidence     numeric,
            merged_into    uuid REFERENCES entity(id) ON DELETE RESTRICT,
            schema_version integer NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Trigram fuzzy name lookup (get_entity_mentions name resolution) — needs pg_trgm (0001).
    op.execute(
        "CREATE INDEX entity_name_trgm_idx ON entity USING gin (canonical_name gin_trgm_ops)"
    )
    op.execute("CREATE INDEX entity_aliases_idx ON entity USING gin (aliases)")
    op.execute("CREATE INDEX entity_identifiers_idx ON entity USING gin (identifiers)")

    # --- mention (entity mentioned in a document) ---
    op.execute(
        """
        CREATE TABLE mention (
            id              uuid PRIMARY KEY,
            entity_id       uuid NOT NULL REFERENCES entity(id) ON DELETE RESTRICT,
            document_id     uuid NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
            mentioned_at    timestamptz,
            confidence      numeric,
            sentiment       numeric,
            context_snippet text,
            model_name      text NOT NULL,
            model_version   text NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX mention_entity_idx ON mention (entity_id, mentioned_at)")
    op.execute("CREATE INDEX mention_document_idx ON mention (document_id)")
    # Idempotent re-enrichment: at most one mention per (entity, document) from a given NER model
    # version, so re-running the same model upserts in place instead of duplicating.
    op.execute(
        "CREATE UNIQUE INDEX mention_uniq "
        "ON mention (entity_id, document_id, model_name, model_version)"
    )

    # --- embedding (pgvector chunk embeddings; fixed dim per deployment, R8) ---
    op.execute(
        """
        CREATE TABLE embedding (
            id            uuid PRIMARY KEY,
            document_id   uuid NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
            chunk_index   integer NOT NULL,
            model_name    text NOT NULL,
            model_version text NOT NULL,
            dim           integer NOT NULL,
            vector        vector(384) NOT NULL,
            source_hash   text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT embedding_chunk_uniq
                UNIQUE (document_id, chunk_index, model_name, model_version)
        )
        """
    )
    op.execute("CREATE INDEX embedding_document_idx ON embedding (document_id)")
    op.execute(
        "CREATE INDEX embedding_ann_idx ON embedding USING hnsw (vector vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding")
    op.execute("DROP TABLE IF EXISTS mention")
    op.execute("DROP TABLE IF EXISTS entity")
