"""``atlas purge <doc_id|entity_id>`` — the audited right-to-erasure path (R3, runbook §6).

The single documented exception to raw-store immutability. Runs the full cascade for a document
(or deletes an entity + its mentions) in one transaction and writes a **retained** `audit_log`
entry. Idempotent: an already-purged / unknown id is a clean no-op. Requires an explicit
``--yes`` confirmation since erasure is destructive (CLAUDE.md: confirm hard-to-reverse actions).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlascore.storage.postgres.base import make_engine
from atlascore.storage.purge import purge_target
from atlascore.storage.rawstore import RawStore


def purge_cmd(
    target_id: Annotated[str, typer.Argument(help="A document id OR entity id (UUID) to erase.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Confirm the (destructive, audited) erasure.")
    ] = False,
) -> None:
    """Erase a document (full cascade incl. raw) or an entity; write a retained audit entry."""
    try:
        uuid = UUID(target_id)
    except ValueError as exc:
        typer.secho(f"not a valid id: {target_id!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if not yes:
        typer.secho(
            "refusing to purge without --yes (erasure is destructive and audited)",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    engine = make_engine()
    try:
        with Session(engine) as session, session.begin():
            result = purge_target(session, RawStore.from_settings(), uuid)
    except OperationalError as exc:
        typer.secho(
            f"database unreachable ({exc.__class__.__name__}); run `make dev && make migrate` first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    if not result.found:
        typer.secho(
            f"nothing to purge for {target_id} (already purged or unknown id)",
            fg=typer.colors.YELLOW,
        )
        return
    if result.target_kind == "document":
        typer.secho(
            f"purged document {target_id}: {result.embeddings} embeddings, "
            f"{result.mentions} mentions, {result.summaries} summaries, "
            f"raw_deleted={result.raw_deleted}, alerts_reconciled={result.alerts_reconciled} "
            f"(purged={result.alerts_purged}). Retained audit entry written.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"purged entity {target_id}: {result.mentions} mentions, "
            f"{result.documents_reconciled} documents reconciled. Retained audit entry written.",
            fg=typer.colors.GREEN,
        )
