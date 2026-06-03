"""initial: required extensions + source table

Creates the required Postgres extensions early (before any later migration that
depends on them) and the ``source`` registry table (docs/schemas/README.md §2.1).

- ``vector`` (pgvector): embeddings ``vector(N)`` columns (B11).
- ``pg_trgm``: trigram GIN index on ``entity.canonical_name`` (B11).

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required extensions (CLAUDE.md §8 / docs/schemas §1). Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("access_method", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column(
            "policy_status",
            sa.Text(),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
        sa.Column(
            "rate_limit",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "retention",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "freshness",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("parser_ref", sa.Text(), nullable=True),
        sa.Column("credential_ref", sa.Text(), nullable=True),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # name= is the naming-convention TOKEN; Alembic's op applies Base.metadata's
        # convention (ck_%(table_name)s_%(constraint_name)s), so these become
        # ck_source_access_method_allowed / ck_source_policy_status_allowed in the DB —
        # matching the ORM model. (Verified: psql shows ck_source_*; drift-check empty.)
        sa.CheckConstraint(
            "access_method IN ('api', 'rss', 'html', 'browser')",
            name="access_method_allowed",
        ),
        sa.CheckConstraint(
            "policy_status IN ('approved', 'restricted', 'pending', 'unknown', 'prohibited')",
            name="policy_status_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source"),
        sa.UniqueConstraint("name", name="uq_source_name"),
    )
    op.create_index(
        "ix_source_category_enabled", "source", ["category", "enabled"], unique=False
    )
    op.create_index("ix_source_policy_status", "source", ["policy_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_source_policy_status", table_name="source")
    op.drop_index("ix_source_category_enabled", table_name="source")
    op.drop_table("source")
    # First migration => downgrade returns to an empty DB; safe to drop extensions.
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
