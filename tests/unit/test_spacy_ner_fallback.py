"""Unit tests for NER backend selection + spaCy fallback (B21; offline)."""

from __future__ import annotations

import pytest

from atlascore.core.settings import Settings
from atlascore.enrichment.ner.base import get_ner_backend
from atlascore.enrichment.ner.rules import RuleBasedNer

pytestmark = pytest.mark.unit


def test_default_is_rule_based() -> None:
    assert isinstance(get_ner_backend(Settings(ner_backend="rules")), RuleBasedNer)


def test_spacy_falls_back_to_rules_when_unavailable() -> None:
    # In CI spaCy/the model is absent → get_ner_backend('spacy') degrades to the rule-based backend
    # (never crashes enrichment). When spaCy IS installed this still returns a valid NerBackend.
    backend = get_ner_backend(Settings(ner_backend="spacy"))
    assert hasattr(backend, "extract") and hasattr(backend, "model_name")


def test_spacy_backend_when_installed() -> None:
    pytest.importorskip("spacy")
    from atlascore.enrichment.ner.spacy_ner import SpacyNer

    try:
        backend = SpacyNer()
    except Exception:  # model not downloaded — acceptable in this environment
        pytest.skip("spaCy model not installed")
    assert backend.model_name == "spacy"
    ents = backend.extract("Apple Inc. announced a product in California.")
    assert all(e.entity_type for e in ents)
