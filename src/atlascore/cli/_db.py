"""Shared CLI DB helper: a session that exits cleanly when no database is reachable."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import typer
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlascore.storage.postgres.base import make_engine


@contextmanager
def session_or_exit() -> Iterator[Session]:
    """Yield a DB session, or exit(1) with a clear message if the database is unreachable."""
    engine = make_engine()
    try:
        with Session(engine) as session:
            yield session
    except OperationalError as exc:
        typer.secho(
            f"database unreachable ({exc.__class__.__name__}); run `make dev && make migrate` first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
