# 0025. B15 hardening + MVP completion: consolidated security suite, end-to-end smoke, scope freeze

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), CLAUDE.md §15 backlog **B15**, §19 (testing standards), §5 (scope discipline); completes the MVP (B1–B15).

## Context

B15 is the final MVP block: prove the whole pipeline runs unattended, consolidate the security
regressions into a clear suite, and ship a README that matches the as-built system. The decisions:

1. **How to express the "security regressions are green" gate** without retagging or duplicating the
   exhaustive case tables already written across B4/B5/B7/B14.
2. **What the end-to-end smoke demo asserts**, given the harness can't depend on a host `pg_dump`
   and the timed scheduler is deliberately out of MVP scope.
3. **Recording MVP completion + the scope freeze** (CLAUDE.md §5: freeze scope until the current set
   runs unattended for a month).

## Decision

- **A `security` pytest marker is the hardening gate (`make security` → `pytest -m security`).** The
  existing focused suites (`test_url_safety.py`, `test_sanitization.py`, `test_sanitize.py`) are
  tagged `security` in addition to `unit`, and a new **`test_security_regressions.py`** is the
  single high-level boundary check: SSRF (private/metadata/rebinding/scheme), HTML sanitization
  (active-content strip), secrets-never-in-DB-columns (`sanitize_for_storage` over the
  `crawl_job`/`audit_log` shapes), prompt-injection-as-evidence (the extractive summarizer copies an
  injection payload as a cited claim, never executes it; active-HTML injection is stripped), and the
  **read-only MCP surface** (exactly the 6 shipped read tools; no `get_raw_document`; no
  write-verb tool). The B14 purge multi-doc-summary regression remains in `test_purge.py`. This
  gives one green gate without moving the detailed tests.
- **An end-to-end `smoke` test is the MVP demo (`make smoke` → `pytest -m smoke`).**
  `tests/integration/test_smoke.py` runs the whole chain unattended with **no live network**
  (respx-mocked feed, injected public resolver): register an approved source → fetch (raw captured)
  → parse → enrich → search → cited summary → alert → MCP `get_alerts` → backup → audited purge. The
  backup step asserts the raw snapshot always and runs the real `pg_dump` only when the binary is
  present (`shutil.which`), so the demo is environment-independent; the full pg_dump/restore is
  covered by B14's live restore drill (`infra/restore_drill.sh`). It needs Postgres 16 and skips
  cleanly when none is reachable (like the integration suite).
- **MVP B1–B15 is complete; scope is frozen.** The README is rewritten to the as-built state (6 read
  tools shipped, deterministic default backends, the deferred list). Deferred-past-MVP items are
  recorded in `docs/STATUS.md` Known issues, not started: the timed scheduler/worker loop (+
  `audit_log` rotation), real embedding/LLM backends, SEC full-filing (Tika) + NVD keyed tier, an
  alert watchlist + lifecycle CLI, `time_series_point` yearly partitions, and the `get_raw_document`
  MCP tool.

## Consequences

- **Positive.** The done-when is met: `make smoke` proves source → ingest → search → MCP summary →
  alert end to end with a backup + purge, and `make security` is a single green hardening gate. The
  MVP is demonstrably complete and the README no longer lies about status.
- **Negative / accepted.** The smoke test's pg_dump step is conditional on the binary (the DB-dump
  half of "with backups" is fully exercised only by the B14 drill, not in every smoke run). The
  `security` marker is a curated suite, not every security-relevant assertion in the tree (the
  connector/worker tests carry their own). Scope freeze means the deferred items wait for a future
  cycle — by design (CLAUDE.md §2.5, §5).
