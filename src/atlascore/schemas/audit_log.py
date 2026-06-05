"""``audit_log`` vocabulary: actor and retention class (docs/schemas §2.11)."""

from __future__ import annotations

from enum import StrEnum


class Actor(StrEnum):
    """Who performed the audited action."""

    SYSTEM = "system"  # scheduler / worker / pipeline
    CLI = "cli"  # an `atlas …` control-plane command
    MCP = "mcp"  # an MCP tool invocation (read-only agent surface)


class RetentionClass(StrEnum):
    """Rotation policy for an audit entry (R14).

    ``routine`` entries are rotated away after the retention window; ``purge`` entries
    (erasure proof) are **retained indefinitely** — the rotation job deletes only
    ``routine`` rows beyond the window.
    """

    ROUTINE = "routine"
    PURGE = "purge"


# Canonical MCP tool-call audit action (docs/mcp §1.4).
MCP_TOOL_CALL = "mcp_tool_call"
# Distinct action when `get_raw_document` emits UNSANITIZED raw HTML (the injection-sensitive path,
# docs/mcp §2.7 / R7) — recorded so raw-HTML disclosures are independently auditable.
MCP_RAW_HTML_RETURNED = "mcp_raw_html_returned"
