"""SQLAlchemy 2.x declarative base, metadata, and engine factory.

A deterministic naming convention makes constraint/index names stable so Alembic
autogenerate is reproducible (the CI drift-check depends on this). Raw blobs live
on disk, never in Postgres (CLAUDE.md §12).
"""

from __future__ import annotations

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from atlascore.core.settings import get_settings

# Stable, predictable names for indexes/constraints (Alembic-friendly).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for all ORM models. ``Base.metadata`` is Alembic's target."""

    metadata = metadata_obj


def make_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine for the single Postgres instance.

    ``url`` defaults to ``Settings.database_url`` (``postgresql+psycopg`` driver).
    """
    return create_engine(url or get_settings().database_url, future=True, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a configured session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
