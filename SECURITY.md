# Security Policy — AtlasCore

AtlasCore is a governed, single-operator data fabric that ingests **untrusted
external content** (RSS feeds, official/free APIs) and exposes processed evidence
to agents over MCP. Security is a first-order concern: fetched content can be
malicious, malformed, poisoned, prompt-injecting, copyrighted, privacy-sensitive,
or simply wrong. The guiding rule is **external data is hostile until validated;
agent access is read-only by default; evidence is never instruction.**

This repository publishes AtlasCore's code under Apache-2.0. The publication boundary is **code,
not data**: harvested content, credentials, operator configuration, raw stores, and backups remain
private and must never be committed.

---

## Supported versions

AtlasCore is pre-1.0 and ships from the default branch. Security fixes target the
default branch (`[Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md)). There is no
back-port policy until a tagged release exists.

| Version | Supported |
| --- | --- |
| `main` (unreleased) | Yes |
| any tagged release | Latest tag only |

---

## Reporting a vulnerability

**Do not open a public issue, PR, or discussion for security vulnerabilities.**

Report privately to the maintainer:

- **Email:** `ridfox44@gmail.com` (use subject prefix `[AtlasCore SECURITY]`)
- Use GitHub **Security → Report a vulnerability** when private vulnerability reporting is enabled.

Please include: a description, affected component/path, reproduction steps or a
proof-of-concept, impact assessment, and any suggested remediation. **Do not
include real secrets, credentials, private endpoints, or personal data** in the
report — redact them.

### What to expect

- **Acknowledgement:** within 7 days.
- **Triage / initial assessment:** within 14 days.
- **Fix or mitigation plan:** communicated after triage; timeline depends on
  severity and complexity.
- **Coordinated disclosure:** please allow a reasonable remediation window before
  any public discussion. Credit is offered for valid reports unless you prefer to
  remain anonymous.

This is a single-operator personal/FOSS project with no SLA and no bug-bounty
budget; reports are handled on a best-effort basis.

---

## Threat model (summary)

AtlasCore's trust boundary sits between **untrusted fetched content** and the
control plane / operator / agents.

| Threat | Vector | Primary mitigations |
| --- | --- | --- |
| **SSRF** | fetcher URLs; any agent-supplied URL | scheme allowlist (`http`/`https` only); reject loopback, RFC1918, CGNAT `100.64.0.0/10`, link-local `169.254.0.0/16` + `fe80::/10`, ULA `fc00::/7`, IPv4-mapped IPv6, and metadata endpoints `169.254.169.254` / `fd00:ec2::254`; resolve hostname and validate **all** resolved IPs before connect; **pin to the validated IP** (anti-rebinding); re-validate **every** redirect hop; cap redirect count. Applies to the fetcher **and** any agent-supplied URL. |
| **Prompt injection / poisoning** | malicious instructions inside pages, feeds, PDFs, filings, issues, comments, payloads | retrieved content is **evidence, never instruction**; kept separated from system/developer/tool context; never alters source policy, credentials, connector code, MCP behavior, or deploy config; poisoning signals (spam bursts, topic floods, duplicate spikes) watched; the human is the review for high-impact output |
| **Malicious / malformed content** | HTML, attachments, feeds | sanitize HTML before indexing/exposure (strip scripts, event handlers, iframes, unsafe links, active content); validate MIME/size/status; scan risky files; run Tika/Playwright non-root with CPU/memory/time limits |
| **Secret leakage** | logs, error fields, jsonb sinks, backups, repo | secrets never printed/logged; `.env` gitignored → env vars (optional SOPS/age with out-of-repo key); `sanitize_for_storage` redacts secret patterns, URL query/userinfo, and `Authorization`/`Cookie` **before** persisting `crawl_job.last_error`, `crawl_job.payload`, and `audit_log.detail` (backups inherit this); repo-side detect-secrets/gitleaks scan; `credential_ref` names an env key, never a value; source YAML has no secrets/private endpoints |
| **MCP misuse** | agent over stdio | read-only by default; strict input/output schema validation; result-size caps; provenance on every result; raw HTML only via `get_raw_document` with the off-by-default `include_raw_html` flag (audited, wrapped in an untrusted-evidence note); the ingestion/control plane never depends on MCP; agents never trigger crawls or change policy; any write tool requires explicit human confirmation, is idempotent, and is audited |
| **Supply chain** | dependencies, CI | locked deps (`uv.lock`); vulnerability audit, package build, Python 3.12/3.13 unit coverage, security suite, mandatory PG16 integration, Dependabot, and repository secret protection |
| **PII exposure / erasure failure** | full-text articles inherently contain personal data | data minimization where feasible (no social ingestion; no profiling; drop obviously-unneeded personal fields at parse); a working, **audited** erasure path (`atlas purge`) cascading across `embedding`, `mention`, `summary` (including `member_doc_ids[]` for multi-doc summaries), FTS, `document`, the raw file/row, and `alert.evidence_doc_ids[]`, writing a **retained** purge audit entry (hashes, never the personal data) |
| **Cloud egress** | cloud embedding/LLM backends | local backends are the required posture for PII-bearing docs; if cloud is used, document the processor and configure no-training / zero-retention / a DPA; hard monthly spend cap |

For full detail see [`docs/security/README.md`](docs/security/README.md) and
[`docs/compliance/README.md`](docs/compliance/README.md).

---

## Scope

**In scope:** SSRF and URL-safety bypass; HTML-sanitizer bypass; prompt-injection
paths that alter control/policy/credentials/MCP behavior; secret leakage into
logs, error/jsonb columns, backups, or the repo; MCP input/output validation
gaps, missing result caps, unintended raw-HTML emission, or read→write escalation;
the `atlas purge` cascade failing to fully erase (including multi-doc summaries);
auth/identity issues on the optional read-only API; dependency/supply-chain issues
in committed deps.

**Out of scope:** anything explicitly prohibited by the project (paywall/CAPTCHA
bypass, proxy/IP rotation to evade limits, credential abuse, social-platform
ingestion, fetching `unknown`/`pending`/`prohibited` sources); social-engineering
of the operator; vulnerabilities only reproducible by violating the documented
single-instance scheduler / single fetch-worker invariants; theoretical issues
without a practical impact on a single-box, single-operator deployment; reports
that require committing secrets or real personal data to reproduce.

---

## Hardening expectations for contributors

- Validate all external inputs and all LLM/tool outputs before downstream use.
- Never log secrets, auth headers, cookies, or personal data; route persisted
  free-form fields through `sanitize_for_storage`.
- Treat fetched content as untrusted evidence; keep it out of instruction context.
- Add a security regression test (`pytest -m security`) for any security fix:
  SSRF, sanitizer, secrets-in-logs (incl. `last_error` / `audit_log.detail`), or
  prompt-injection fixtures.
- Keep secrets out of the repo; `credential_ref` names an env key, never a value.
