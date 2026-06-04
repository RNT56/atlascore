"""``atlas mcp`` — run the read-only MCP server over stdio (B7).

`atlas mcp dev` launches the FastMCP server on **stdio** for an MCP client / agent host to drive.
It is read-only (the control plane never depends on MCP) and the engine connects lazily on the
first tool call, so the server starts even before the database is reached.
"""

from __future__ import annotations

import typer

from atlascore.mcp_server import build_mcp_server

mcp_app = typer.Typer(
    name="mcp",
    help="Run the read-only MCP server (stdio).",
    no_args_is_help=True,
)


@mcp_app.command("dev")
def dev() -> None:
    """Run the MCP server over stdio (for development / an MCP client host)."""
    build_mcp_server().run(transport="stdio")
