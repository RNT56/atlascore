"""``atlas source`` — the source-registry control plane (B2).

- ``validate`` is pure (no DB): schema + duration grammar + default-deny policy gate.
  It backs the CI ``source-yaml`` job (``atlas source validate --all``).
- ``sync`` / ``test`` / ``list`` need a database; they skip-fail with a clear message
  when none is reachable. ``test`` is a policy dry-run — live reachability lands with the
  connector in B4 (the ingestion plane never depends on a network call here).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlascore.core.errors import SourceValidationError
from atlascore.schemas.source import PolicyStatus
from atlascore.storage.postgres.base import make_engine
from atlascore.storage.sources import (
    DEFAULT_REGISTRY_DIR,
    discover_source_files,
    get_source,
    list_sources,
    load_source_yaml,
    upsert_source,
)

source_app = typer.Typer(
    name="source",
    help="Source registry: validate, sync (register), test, and list sources.",
    no_args_is_help=True,
)

_LoadError = (SourceValidationError, ValidationError)


@contextmanager
def _session() -> Iterator[Session]:
    """Yield a DB session, exiting cleanly with a message if the database is unreachable."""
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


def _select_files(path: Path | None, all_: bool) -> list[Path]:
    if all_ and path is not None:
        typer.secho("provide a path OR --all, not both", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if all_:
        files = discover_source_files()
        if not files:
            typer.echo(f"no source files found in {DEFAULT_REGISTRY_DIR}/")
        return files
    if path is not None:
        return [path]
    typer.secho("provide a path or --all", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


@source_app.command("validate")
def validate(
    path: Annotated[Path | None, typer.Argument(help="A source YAML file to validate.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Validate every YAML in sources/.")] = False,
) -> None:
    """Validate source YAML: schema, duration grammar, and the default-deny policy gate."""
    files = _select_files(path, all_)
    failures = 0
    for file in files:
        try:
            source = load_source_yaml(file)
        except _LoadError as exc:
            failures += 1
            typer.secho(f"FAIL  {file}", fg=typer.colors.RED, err=True)
            typer.echo(f"      {exc}", err=True)
            continue
        record = source.to_record()
        verdict = (
            "fetchable"
            if record.is_fetchable
            else f"disabled (policy={source.policy.status.value}, enabled={source.enabled})"
        )
        typer.secho(f"OK    {file}  [{source.id}] {verdict}", fg=typer.colors.GREEN)
    if failures:
        typer.secho(f"{failures} source file(s) failed validation", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@source_app.command("sync")
def sync(
    path: Annotated[Path | None, typer.Argument(help="A source YAML file to register.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Register every YAML in sources/.")] = False,
) -> None:
    """Validate and register (upsert) source YAML into the `source` table (idempotent)."""
    files = _select_files(path, all_)
    if not files:
        return
    # Validate everything first so a bad file aborts before any DB write.
    records = []
    for file in files:
        try:
            records.append(load_source_yaml(file).to_record())
        except _LoadError as exc:
            typer.secho(f"FAIL  {file}\n      {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    with _session() as session, session.begin():
        for record in records:
            upsert_source(session, record)
            typer.secho(f"registered  {record.name}", fg=typer.colors.GREEN)
    typer.echo(f"synced {len(records)} source(s)")


@source_app.command("test")
def test(
    source_id: Annotated[str, typer.Argument(help="The source id (slug) or name.")],
) -> None:
    """Dry-run a registered source: the default-deny policy gate. No live network (B2)."""
    with _session() as session:
        row = get_source(session, source_id)
        if row is None:
            typer.secho(
                f"source {source_id!r} is not registered (run: atlas source sync)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        status = PolicyStatus(row.policy_status)
        if not (row.enabled and status.is_fetchable):
            reason = "disabled" if not row.enabled else f"policy={status.value}"
            typer.secho(
                f"REFUSED  {row.name}: {reason} (default-deny)",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)
        config = row.config or {}
        typer.secho(f"OK  {row.name} is fetchable", fg=typer.colors.GREEN)
        typer.echo(f"  connector: {config.get('connector')}")
        typer.echo(f"  schedule : {config.get('schedule')}")
        typer.echo("  (live reachability check lands with the connector in B4)")


@source_app.command("list")
def list_registered() -> None:
    """List registered sources (slug, policy, fetchability, name)."""
    with _session() as session:
        rows = list_sources(session)
        if not rows:
            typer.echo("no sources registered")
            return
        for row in rows:
            slug = (row.config or {}).get("slug", "-")
            fetchable = row.enabled and PolicyStatus(row.policy_status).is_fetchable
            state = "fetchable" if fetchable else "disabled"
            typer.echo(f"{slug:<28} {row.policy_status:<11} {state:<10} {row.name}")
