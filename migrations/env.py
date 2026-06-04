"""Alembic environment.

Targets ``Base.metadata`` (all ORM models) for autogenerate, and resolves the
database URL from the environment via AtlasCore ``Settings`` so no credentials
live in a committed file. Supports both offline (``--sql``) and online runs.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models package registers every table on Base.metadata.
import atlascore.storage.postgres.models  # noqa: F401  (side-effect: model registration)
from atlascore.core.settings import get_settings
from atlascore.storage.postgres.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Prefer an explicit alembic.ini URL, else resolve from Settings/DATABASE_URL."""
    url = config.get_main_option("sqlalchemy.url")
    return url or get_settings().database_url


# Expression / partial / GIN indexes that Alembic autogenerate cannot reliably reflect or
# compare (functional ``to_tsvector`` index, partial-unique ``WHERE`` index, array GINs).
# They are created via raw SQL in their migration and are NOT declared on the ORM model;
# excluding their names here keeps the autogenerate drift-check (CI) from emitting spurious
# create/drop for them on either the model or the reflected DB side. See migration 0003.
_MANUAL_INDEXES = frozenset(
    {
        "uq_document_canonical_url",
        "ix_document_fts",
        "ix_document_topics",
        "ix_document_entity_ids",
    }
)


# Whole tables created via raw SQL (autogenerate can't round-trip them) — every object owned by
# one of these tables is skipped by the drift-check, the way ``time_series_point`` (0006) is:
#   - ``entity``    — trigram ``gin (canonical_name gin_trgm_ops)`` + array GINs (need pg_trgm)
#   - ``mention``   — for DDL consistency with its siblings
#   - ``embedding`` — ``pgvector vector(N)`` column + HNSW ANN index
#   - ``summary``   — ``GIN(member_doc_ids)`` array index (migration 0008)
# The ORM models map their columns for querying/upserts only; the real DDL lives in the migrations.
_RAW_SQL_TABLES = frozenset({"entity", "mention", "embedding", "summary"})


def _is_raw_sql_table(table_name: str | None) -> bool:
    """True for a table whose DDL is hand-written in a migration (not autogenerate-managed)."""
    if table_name is None:
        return False
    return table_name.startswith("time_series_point") or table_name in _RAW_SQL_TABLES


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Skip manually-managed objects during autogenerate.

    - The expression/partial/GIN indexes created via raw SQL (migration 0003).
    - The whole ``time_series_point`` partitioned table (0006) and the enrichment tables
      ``entity``/``mention``/``embedding`` (0007) — created via raw SQL because autogenerate cannot
      emit ``PARTITION BY``, trigram/array GINs, or a pgvector ``vector(N)``/HNSW index. We match by
      the **owning table** (not a bare name prefix) so a normal column like ``document.entity_ids``
      is still drift-checked.
    """
    if type_ == "index" and name in _MANUAL_INDEXES:
        return False
    if type_ == "table":
        return not _is_raw_sql_table(name)
    table_name = getattr(getattr(obj, "table", None), "name", None)
    return not _is_raw_sql_table(table_name)


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
