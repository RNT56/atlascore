"""``atlas`` console entry point.

B1 provides the app skeleton and ``atlas version`` / ``atlas --version``. Later
blocks register the operational commands (CLAUDE.md §9).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Annotated

import typer

from atlascore.cli.crawl import crawl_app
from atlascore.cli.enrich import enrich_cmd
from atlascore.cli.health import digest_cmd, health_cmd
from atlascore.cli.mcp import mcp_app
from atlascore.cli.parse import parse_raw
from atlascore.cli.search import search_cmd
from atlascore.cli.source import source_app

app = typer.Typer(
    name="atlas",
    help="AtlasCore control plane — the single-operator ingestion-management surface.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(source_app, name="source")
app.add_typer(crawl_app, name="crawl")
app.add_typer(mcp_app, name="mcp")
app.command(name="parse")(parse_raw)
app.command(name="enrich")(enrich_cmd)
app.command(name="search")(search_cmd)
app.command(name="health")(health_cmd)
app.command(name="digest")(digest_cmd)


def _version() -> str:
    """Resolve the installed distribution version, falling back to the package constant."""
    try:
        return _dist_version("atlascore")
    except PackageNotFoundError:  # not installed (e.g. running from a source tree)
        from atlascore import __version__

        return __version__


def _print_version() -> None:
    typer.echo(f"atlascore {_version()}")


@app.callback(invoke_without_command=True)
def main(
    *,
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Show the AtlasCore version and exit.")
    ] = False,
) -> None:
    """AtlasCore control plane."""
    if version:
        _print_version()
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print the AtlasCore version."""
    _print_version()


if __name__ == "__main__":  # pragma: no cover
    app()
