# 0029. B18 alert maturity: watchlist gating (CVSS) + lifecycle CLI

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §11.10 (alert), §13/§17; docs/mcp §2.6/§3; post-MVP cycle B18 (builds on B13 alerts).

## Context

B13 shipped four rule-based alerts, but `cve_watch`/`sec_filing` fired on **every** document in the
scan window (no filtering), the NVD connector dropped CVSS entirely, and alerts had no lifecycle
beyond `open`. The forced decisions:

1. **Where the watchlist lives** — a DB table, `source.config.params`, or a git-tracked file.
2. **How to surface CVSS** to the rule without a schema change.
3. **How to advance alert lifecycle** without a new column.

## Decision

- **Watchlist = a git-tracked `watchlist.yaml`** (code-not-data, like `sources/`, ADR 0013) — not a
  DB table (overkill, and it's cross-source *policy*, not per-source) and not
  `source.config.params` (which is `dict[str,str]` and can't hold lists/floats). Pydantic v2 models
  (`schemas/watchlist.py`, `extra='forbid'`, CIK zero-normalized, CVSS bounded 0–10);
  `storage.watchlist.load_watchlist` returns `Watchlist.empty()` when the file is absent.
  `WATCHLIST_PATH` relocates it.
- **CVSS surfaced via a parser-safe body marker** (no schema change): the NVD connector prepends
  `CVSS base score <n> (<SEVERITY>).` to the description (v3.1→v3.0→v2, severity derived from band
  when absent); the `cve_watch` rule reads it with a regex and sets the alert severity from the CVSS
  band.
- **Gating is backward-compatible.** `build_rules(watchlist)` constructs the document rules gated by
  an *active* watchlist; an absent/inactive watchlist (`enabled:false` or no criteria) preserves the
  pre-B18 fire-on-everything behavior. `cve_watch` fires on `CVSS ≥ min_cvss`, watched severity,
  matched `cve_id`, or keyword; `sec_filing` fires on watched CIK (+ optional form). `ALL_RULES`
  stays as the ungated constant for tests; `run_alerts` defaults to `build_rules(load_watchlist())`.
- **Lifecycle reuses the existing `alert.status` column** (no migration): `storage.alerts.
  acknowledge_alert` (open→acknowledged) and `resolve_alert` (open|acknowledged→resolved) via a
  guarded `_transition` that raises `AlertTransitionError` on an illegal/terminal move (`purged` can
  never transition). `atlas alerts ack|resolve|list` drive it; `list` reuses the `get_alerts` read
  path. **MCP `get_alerts` is unchanged** (read-only; lifecycle is CLI/pipeline only).

## Consequences

- **Positive.** Alerts are now precision-gated (watched issuers / critical CVEs) with CVSS-derived
  severity, while a box without a watchlist behaves exactly as before; the operator can triage via a
  real lifecycle. No migration, no new table. The git-tracked watchlist keeps policy reviewable.
- **Negative / accepted.** CVSS rides in the document body (a marker) rather than a typed column —
  fine for gating, and avoids a migration; a future typed field is possible. SEC form matching parses
  the form from `doc.title` (`"<form> — <name>"`). The watchlist is read fresh each `run_alerts`
  (cheap). Lifecycle is forward-only (`open→acknowledged→resolved`); reopening is out of scope.
