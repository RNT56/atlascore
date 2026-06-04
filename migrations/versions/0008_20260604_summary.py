"""summary (cached, cited LLM summaries, B12)

Creates the ``summary`` table (docs/schemas/README.md §2.5) via raw SQL — the
``GIN(member_doc_ids)`` array index (required so the purge cascade can find multi-doc summaries by
member, R3/R4) doesn't round-trip through Alembic autogenerate. The R4 content-hash cache key is
``UNIQUE (source_hash, model_name, model_version, prompt_version)``: an identical
``(content, model, prompt)`` tuple is never re-summarized. The whole ``summary`` table is excluded
from the autogenerate drift-check by owning table in ``migrations/env.py``.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE summary (
            id              uuid PRIMARY KEY,
            scope           text NOT NULL CHECK (scope IN ('single', 'multi')),
            document_id     uuid REFERENCES document(id) ON DELETE RESTRICT,
            member_doc_ids  text[] NOT NULL,
            source_hash     text NOT NULL,
            model_name      text NOT NULL,
            model_version   text NOT NULL,
            prompt_version  text NOT NULL,
            summary_text    text NOT NULL,
            citations       jsonb NOT NULL DEFAULT '{}'::jsonb,
            token_cost      integer,
            created_at      timestamptz NOT NULL DEFAULT now(),
            schema_version  integer NOT NULL,
            CONSTRAINT summary_cache_key
                UNIQUE (source_hash, model_name, model_version, prompt_version)
        )
        """
    )
    op.execute("CREATE INDEX summary_document_idx ON summary (document_id)")
    # Purge (R3) scans this to find multi-doc summaries (document_id is null) by member id.
    op.execute("CREATE INDEX summary_member_ids_idx ON summary USING gin (member_doc_ids)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS summary")
