"""raw_document origin URL and local parser provenance

Secondary entry payloads such as SEC primary filings are fetched by the single fetch role, stored
immutably, and parsed later without network access. These nullable fields make that raw object
self-describing and keep manual reparse/provenance possible. Existing raw rows remain valid.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-12 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("raw_document", sa.Column("origin_url", sa.Text(), nullable=True))
    op.add_column("raw_document", sa.Column("parser_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("raw_document", "parser_ref")
    op.drop_column("raw_document", "origin_url")
