"""The read-only MCP server (FastMCP over stdio). See ``docs/mcp/README.md``."""

from __future__ import annotations

from atlascore.mcp_server.server import (
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    TOOL_SEARCH_DOCUMENTS,
    build_mcp_server,
)

__all__ = [
    "SEARCH_LIMIT_DEFAULT",
    "SEARCH_LIMIT_MAX",
    "TOOL_SEARCH_DOCUMENTS",
    "build_mcp_server",
]
