"""document

Creates the ``document`` canonical record table (docs/schemas/README.md §2.4).

The plain btree indexes are autogenerate-managed (declared on the ORM model). The
partial-unique ``canonical_url`` index, the FTS ``tsvector`` GIN, and the
``topics``/``entity_ids`` array GINs are expression/partial indexes Alembic autogenerate
cannot round-trip, so they are created here via raw SQL and excluded from the drift-check
by ``_include_object`` in ``migrations/env.py``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("raw_document_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column(
            "topics",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "entity_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_local_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_document_id"],
            ["raw_document.id"],
            name=op.f("fk_document_raw_document_id_raw_document"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_document_source_id_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
    )
    op.create_index("ix_document_cluster_id", "document", ["cluster_id"], unique=False)
    op.create_index("ix_document_published_at", "document", ["published_at"], unique=False)
    op.create_index(
        "ix_document_source_id_published_at",
        "document",
        ["source_id", "published_at"],
        unique=False,
    )

    # --- Expression / partial / GIN indexes (raw SQL; excluded from autogenerate). ---
    # Partial unique: real canonical URLs dedupe; many null-URL docs coexist (§2.4).
    op.execute(
        "CREATE UNIQUE INDEX uq_document_canonical_url "
        "ON document (canonical_url) WHERE canonical_url IS NOT NULL"
    )
    # Full-text search over the parsed body (B6 query path builds on this).
    op.execute(
        "CREATE INDEX ix_document_fts "
        "ON document USING gin (to_tsvector('simple', coalesce(body_text, '')))"
    )
    # Array GINs for topic / entity filtering (populated by enrichment, B11).
    op.execute("CREATE INDEX ix_document_topics ON document USING gin (topics)")
    op.execute("CREATE INDEX ix_document_entity_ids ON document USING gin (entity_ids)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_entity_ids")
    op.execute("DROP INDEX IF EXISTS ix_document_topics")
    op.execute("DROP INDEX IF EXISTS ix_document_fts")
    op.execute("DROP INDEX IF EXISTS uq_document_canonical_url")
    op.drop_index("ix_document_source_id_published_at", table_name="document")
    op.drop_index("ix_document_published_at", table_name="document")
    op.drop_index("ix_document_cluster_id", table_name="document")
    op.drop_table("document")
