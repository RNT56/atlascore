# 0026. B15 polish: ship `get_raw_document` — the 7th MCP tool, completing the surface

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §13 (the 7-tool MVP surface, "all 7 ship in MVP"), docs/mcp §2.7 + R7 (`get_raw_document`), §14/§15 (sanitization, untrusted evidence); follows [ADR 0025](0025-b15-hardening-and-mvp-completion.md).

## Context

ADR 0025 declared the MVP complete but shipped **6** of the 7 mandated MCP tools, deferring
`get_raw_document`. CLAUDE.md §13 is explicit that **all 7 ship in MVP**, so "MVP complete with 6
tools" was a real contradiction between the contract and the as-built state — the single remaining
MVP spec gap. A polish pass is the natural place to close it: `get_raw_document` is a read tool over
existing stores (no new source/service, so it does not violate the §5 scope freeze — it *finishes*
MVP scope rather than expanding it).

The design tension: `raw_document` is the immutable payload a document was **parsed from**, and one
fetch (an RSS feed / SEC submissions blob) backs **several** documents — so there is no per-document
raw HTML, only the shared source payload.

## Decision

Ship `get_raw_document` (R7) as `atlascore.storage.raw_documents.get_raw_document_content` + the MCP
tool:

- **Resolve** by `document_id` (preferred; also yields `canonical_url`) or directly by
  `raw_document_id`; read the immutable content-addressed blob via `RawStore`. A bad/unknown id
  returns `found=false` (never a crash).
- **Sanitized by default.** `include_raw_html=false` returns `content_kind=sanitized_text` (the raw
  payload run through the nh3 `strip_html` boundary — scripts/handlers/iframes/active content gone).
  `include_raw_html=true` returns `content_kind=raw_html`: the **verbatim** payload wrapped with an
  `untrusted_evidence_notice`. **This is the only tool that ever emits raw HTML**, and only behind
  the flag.
- **Byte-capped** by `max_bytes` (1 KiB–1 MiB, default 256 KiB); over-cap content is UTF-8
  byte-truncated with `truncated=true` and the full `size_bytes` reported in provenance.
- **Audited on every call**; the raw-HTML disclosure uses a **distinct** audit action,
  `mcp_raw_html_returned` (vs `mcp_tool_call`), so raw-HTML reads are independently auditable.
- **Shared-raw honesty:** `include_raw_html` returns the *source* payload the document was parsed
  from, which may contain sibling items' bytes — it is source evidence, not a per-item
  reconstruction. Documented in the tool output semantics + docs/mcp §2.7.

The sanitize/raw decision lives entirely in the tool; `storage.raw_documents` only surfaces the raw
bytes + provenance, keeping one place responsible for emitting raw HTML.

## Consequences

- **Positive.** The MCP surface now ships **all 7 MVP tools** — CLAUDE.md §13 and the as-built
  system agree; the MVP is genuinely complete. The injection-sensitive path is sanitized-by-default,
  gated, wrapped, byte-capped, and separately audited, exactly as R7/§13 require. Covered by unit
  (contract + payload/truncation helpers, tagged `security`) and integration (sanitized-vs-raw +
  the `mcp_raw_html_returned` audit) tests; the consolidated security suite's MCP-surface assertion
  now expects all 7 tools.
- **Negative / accepted.** With shared raws, `include_raw_html` can include sibling-item bytes (no
  per-item raw reconstruction in the MVP data model) — documented. Otherwise no new surface: it
  reads existing stores and adds one audited read tool.
