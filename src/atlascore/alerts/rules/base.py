"""The alert-rule interface (docs/mcp §3, CLAUDE.md §11.10).

A rule evaluates freshly-processed data and emits :class:`AlertCandidate`s. Rules are **pure
readers** — they never deliver or persist; the worker (`job_type=alert`) persists candidates
idempotently on their deterministic ``dedupe_key`` (re-evaluating the same fact never double-fires)
and the delivery step sends them. ``detail`` is sanitized before persistence (§14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AlertCandidate:
    """One would-be alert. ``dedupe_key`` is deterministic so creation is idempotent."""

    alert_type: str
    rule_id: str
    severity: str
    title: str
    dedupe_key: str
    evidence_doc_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AlertRule(Protocol):
    """Evaluates against processed stores and yields alert candidates. Holds no per-call state."""

    @property
    def rule_id(self) -> str: ...

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        """Return the alert candidates this rule currently matches (read-only)."""
        ...
