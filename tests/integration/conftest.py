"""Integration-test fixtures: a real Postgres engine, skipped when none is reachable.

`make test` (all tests) stays green offline because these fixtures call ``pytest.skip``
when no database answers. The CI ``integration`` job (and ``make test-integration`` after
``make dev``) provides a Postgres 16 + pgvector service via ``DATABASE_URL``, so they run.
No live external network — only the local DB service.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

_DEFAULT_URL = "postgresql+psycopg://atlas:atlas@localhost:5432/atlascore"


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_URL)


@pytest.fixture(scope="session")
def db_engine(database_url: str) -> Iterator[Engine]:
    """A live engine, or skip the whole integration suite if the DB is unreachable."""
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"no reachable database at {database_url}: {exc.__class__.__name__}")
    yield engine
    engine.dispose()
