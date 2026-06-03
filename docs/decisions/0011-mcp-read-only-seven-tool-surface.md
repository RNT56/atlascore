# 0011. Expose a read-only seven-tool MCP surface over stdio with injection safety and side-effect confirmation instead of RBAC

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), section 9 (MCP tool surface), section 5 (control plane independence), section 7 (env vars), section 2.11 (`audit_log`); corrections 6 (SSRF), 7 (`get_raw_document` flag), 10 (B7/B8 build order); MVP blocks B7, B8, B9, B11, B12, B13.

> ADRs are immutable once Accepted. To change this decision, write a new ADR and
> mark this one *Superseded by*. Where this file and the canonical spec disagree,
> the spec wins and this ADR must be corrected.

## Context

AtlasCore exposes its processed data to the operator's own agents and dashboards
over MCP. The forces:

- **Single operator.** There is exactly one human/principal. Multi-tenant
  authorization (RBAC, OIDC, an identity provider) would be pure speculative
  scope. The controls that actually matter are **prompt-injection safety** and
  **explicit confirmation on any side effect**, not per-role permissions.
- **Retrieved content is hostile.** Document bodies, raw HTML, feeds, filings,
  and payloads can carry prompt-injection. MCP results are *evidence*, never
  instructions, and must never be able to alter source policy, credentials,
  connector code, MCP behavior, or deployment config.
- **Control-plane independence.** The ingestion/control plane must not depend on
  MCP; agents must never trigger crawls or change source policy (section 5). MCP
  is read-out, not control-in.
- **Provenance-first.** Every result must carry source, document ID,
  published/fetched time, and confidence so an agent's answer is citable.

## Decision

AtlasCore ships an MCP server (`apps/mcp_server`, FastMCP) **over stdio**,
**read-oriented**, with **strict input/output schema validation**, **result-size
caps**, **audit logging**, and **provenance on every result**. It deliberately
has **no RBAC / no IdP**; the security model is injection safety + side-effect
confirmation.

### The seven MVP tools

| Tool | Purpose | Notes |
| --- | --- | --- |
| `search_documents` | FTS + filters over `document` | sanitized text + provenance; **never raw HTML** |
| `get_macro_series` | a `time_series_point` series + observations | FRED in MVP |
| `get_entity_mentions` | mentions of an entity across documents | alias-aware; returns confidence |
| `get_document_summary` | cached, cited summary of one OR many docs | reads `summary` table; `LLM_BACKEND`-aware; returns `null` + reason if none cached and backend cannot/should not compute |
| `get_source_health` | freshness/error/parse-quality per source | **no secrets, no internal hostnames**; read-only convenience, not the operational gate |
| `get_alerts` | recent alerts + `evidence_doc_ids` | read-only |
| `get_raw_document` | raw text/HTML for one doc | the injection-sensitive tool; sanitized-by-default (see flag below); **raw HTML ONLY via this tool** |

Optional later (each its own future ADR): `get_timeline`, `query_graph` (only if
an entity graph is built), and `subscribe_alert` (a **write** tool requiring
explicit human confirmation each invocation, idempotent, audited).

### `get_raw_document` behaviour (correction 7)

The single injection-sensitive surface is resolved with an off-by-default flag:

- Input includes `include_raw_html: false` (default).
- Default → returns **sanitized text only**.
- `include_raw_html: true` → returns the **unsanitized raw HTML**, MUST be
  **audited** (`audit_log` with `action` + `tool_name`), and MUST be wrapped with
  an explicit *"untrusted evidence, not instructions"* provenance note.
- Reaffirmed: **only this tool, only with the flag set, ever emits raw HTML.**
  No other tool returns raw markup.

### SSRF / URL-safety (correction 6)

Any agent-supplied URL (and the fetcher) goes through the URL-safety check in
`packages/security/url_safety` before connect:

- **Scheme allowlist:** `http`/`https` only.
- **Reject:** loopback; RFC1918; CGNAT `100.64.0.0/10`; link-local
  `169.254.0.0/16` and `fe80::/10`; ULA `fc00::/7`; IPv4-mapped IPv6; metadata
  endpoints `169.254.169.254` and `fd00:ec2::254`.
- **Resolve the hostname and validate ALL resolved IPs before connect, then pin
  the connection to a validated IP** (anti-DNS-rebinding).
- **Re-validate every redirect hop** and **cap the redirect count.**

MCP never fetches an arbitrary agent-supplied URL unless URL-safety **and**
source policy both pass.

### Control-plane independence and the health gate (correction 10)

`get_source_health` is read-only convenience for agents. The **operational**
health surface is CLI-driven and MCP-independent: `atlas health` / `atlas digest`
reads `source_health` + `crawl_job` DLQ depth and emits the daily digest via the
alert delivery channel. The control plane never depends on MCP.

### Build order (correction 10)

- **B7** ships `search_documents`, creates the **`audit_log` table**, and
  enforces result caps + audit logging on the MCP path. (B7 does **not** ship
  `get_source_health`.)
- **B8** introduces `source_health` + the freshness job, then adds
  `get_source_health`. The dependency is explicit: `get_source_health` reads the
  `source_health` table, which does not exist until B8.
- The remaining tools land with their data: `get_macro_series` (B9),
  `get_entity_mentions` (B11), `get_document_summary` (B12), `get_alerts` (B13).
  `get_raw_document` is available once documents exist (B7-era), governed by the
  flag above.

### Security posture

- Runs over **stdio**; credentials come from the environment and are **never
  printed**. (If ever exposed over HTTP, it sits behind Tailscale/WireGuard or a
  static token — not an IdP.)
- Strict schema validation on inputs and outputs; result sizes and token volume
  are capped.
- Retrieved content is treated as untrusted evidence, never instruction, and is
  kept separated from system/developer/tool instructions.
- Every result returns provenance (source, document ID, published/fetched time,
  confidence).

## Consequences

- **Positive:**
  - The attack surface is small and explicit: one stdio server, seven read tools,
    one flag-gated raw-HTML escape hatch.
  - No speculative auth machinery; effort goes to the controls that matter for a
    single operator (injection safety, side-effect confirmation, SSRF).
  - Agents get citable, provenance-bearing answers without any path to mutate the
    pipeline.
  - Build order is dependency-honest (B7 creates `audit_log`; B8 adds
    `source_health` + `get_source_health`).

- **Negative / costs:**
  - No per-tool/per-caller authorization; if the project ever becomes
    multi-principal this ADR must be superseded.
  - The raw-HTML path is inherently risky; it is mitigated by default-off +
    audit + provenance wrapper, not eliminated.
  - SSRF correctness depends on validating every resolved IP and every redirect
    hop, which must be tested (B15 regressions).

- **Follow-ups / obligations:**
  - Implement the seven tools across B7–B13 with strict schemas, result caps,
    provenance, and audit logging.
  - Implement the `include_raw_html` flag, its audit entry, and the untrusted-
    evidence wrapper on `get_raw_document` (B7).
  - Implement SSRF/URL-safety in `packages/security/url_safety` and apply it to
    the fetcher and any agent-supplied URL (B4) with B15 regression fixtures.
  - Implement `atlas health` / `atlas digest` as the MCP-independent operational
    gate (B8).
  - Document the tool surface in `docs/mcp/README.md`, consistent with this ADR.

- **Revisit triggers:**
  - Any multi-principal / shared-access scenario (RBAC/IdP becomes in scope; new
    ADR).
  - Adding `subscribe_alert` or any other **write** tool (confirm-each-time,
    idempotent, audited; own ADR).
  - Building an entity graph (would justify `query_graph`; own ADR).
  - Exposing MCP over HTTP (revisit transport security).
