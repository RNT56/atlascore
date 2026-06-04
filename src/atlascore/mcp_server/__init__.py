"""The read-only MCP server (FastMCP over stdio). See ``docs/mcp/README.md``."""

from __future__ import annotations

from atlascore.mcp_server.server import (
    HEALTH_LIMIT_DEFAULT,
    HEALTH_LIMIT_MAX,
    MACRO_LIMIT_DEFAULT,
    MACRO_LIMIT_MAX,
    MENTIONS_LIMIT_DEFAULT,
    MENTIONS_LIMIT_MAX,
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    TOOL_GET_ENTITY_MENTIONS,
    TOOL_GET_MACRO_SERIES,
    TOOL_GET_SOURCE_HEALTH,
    TOOL_SEARCH_DOCUMENTS,
    build_mcp_server,
)

__all__ = [
    "HEALTH_LIMIT_DEFAULT",
    "HEALTH_LIMIT_MAX",
    "MACRO_LIMIT_DEFAULT",
    "MACRO_LIMIT_MAX",
    "MENTIONS_LIMIT_DEFAULT",
    "MENTIONS_LIMIT_MAX",
    "SEARCH_LIMIT_DEFAULT",
    "SEARCH_LIMIT_MAX",
    "TOOL_GET_ENTITY_MENTIONS",
    "TOOL_GET_MACRO_SERIES",
    "TOOL_GET_SOURCE_HEALTH",
    "TOOL_SEARCH_DOCUMENTS",
    "build_mcp_server",
]
