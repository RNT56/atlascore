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

from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings

_log = get_logger("atlascore.enrichment.ner")


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


def get_ner_backend(settings: Settings | None = None) -> NerBackend:
    """Resolve the NER backend by ``NER_BACKEND`` (B21).

    ``rules`` → the deterministic rule-based extractor (default, always available). ``spacy`` →
    the spaCy small-model backend (``local-embeddings`` extra); if spaCy or the model is unavailable
    it **falls back** to the rule-based extractor with a logged warning — enrichment never crashes.
    A different backend writes a distinct ``model_name``/``version``, so backends coexist cleanly
    (the pipeline clears by model identity; no schema change).
    """
    settings = settings or get_settings()
    from atlascore.enrichment.ner.rules import RuleBasedNer

    if settings.ner_backend == "spacy":
        try:
            from atlascore.enrichment.ner.spacy_ner import SpacyNer

            return SpacyNer(settings=settings)
        except Exception as exc:  # spaCy/model missing → degrade, don't crash enrichment
            _log.warning("ner_backend_fallback", requested="spacy", error=str(exc))
            return RuleBasedNer()
    return RuleBasedNer()
