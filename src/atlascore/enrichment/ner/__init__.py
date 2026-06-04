"""Named-entity recognition backends (CLAUDE.md §8/§20, docs/schemas §2.8)."""

from __future__ import annotations

from atlascore.enrichment.ner.base import EntityCandidate, NerBackend, get_ner_backend
from atlascore.enrichment.ner.rules import RuleBasedNer, normalize_name

__all__ = [
    "EntityCandidate",
    "NerBackend",
    "RuleBasedNer",
    "get_ner_backend",
    "normalize_name",
]
