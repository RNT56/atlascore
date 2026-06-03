"""``atlas`` console entry point.

B1 provides the app skeleton and ``atlas version`` / ``atlas --version``. Later
blocks register the operational commands (CLAUDE.md §9).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

import typer

app = typer.Typer(
    name="atlas",
    help="AtlasCore control plane — the single-operator ingestion-management surface.",
    no_args_is_help=True,
    add_completion=False,
)


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
    version: bool = typer.Option(
        False, "--version", "-V", help="Show the AtlasCore version and exit."
    ),
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
