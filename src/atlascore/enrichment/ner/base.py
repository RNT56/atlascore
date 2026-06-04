"""The NER backend interface + factory (CLAUDE.md §8/§20, docs/schemas §2.8/§2.9).

A NER backend extracts :class:`EntityCandidate`s from a document's text. MVP ships the
deterministic, dependency-free :class:`RuleBasedNer` (capitalized-phrase + ticker/CIK patterns) so
the entity surface works offline and in CI. A spaCy small-model backend is a pluggable upgrade
behind the existing ``local-embeddings`` extra (``spacy>=3.7``) — same interface, no schema change.
See ADR 0021.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from atlascore.core.errors import ConfigError


@dataclass(frozen=True)
class EntityCandidate:
    """One extracted entity mention candidate.

    ``normalized_name`` is the case/whitespace-folded lookup key used to find-or-create the
    canonical ``entity``; ``identifiers`` carries any structured ids found (e.g. ``{"ticker":
    "AAPL"}``, ``{"cik": "0000320193"}``); ``confidence`` is the extractor's heuristic certainty.
    """

    text: str
    entity_type: str
    normalized_name: str
    confidence: float
    identifiers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class NerBackend(Protocol):
    """Extracts entity candidates from text. Implementations hold no per-call state."""

    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    def extract(self, text: str) -> list[EntityCandidate]:
        """Return the de-duplicated entity candidates found in ``text`` (order is significant)."""
        ...


def get_ner_backend() -> NerBackend:
    """Resolve the NER backend. MVP always returns the deterministic rule-based extractor."""
    from atlascore.enrichment.ner.rules import RuleBasedNer

    return RuleBasedNer()


def _unreachable_cloud_ner() -> NerBackend:  # pragma: no cover - reserved for a future spaCy swap
    raise ConfigError("only the rule-based NER backend is wired in MVP (see ADR 0021)")
