"""Unit tests for the deterministic enrichment backends (offline; no DB, no network).

Pins the rule-based NER (golden output), topic tagging, language fallback, the hashing embedder
(determinism + dim + normalization), and the chunker. The backends must stay deterministic so the
R13 re-embed skip and the entity surface are reproducible.
"""

from __future__ import annotations

import math

import pytest

from atlascore.enrichment.embeddings import HashingEmbeddingBackend, get_embedding_backend
from atlascore.enrichment.language import detect_language
from atlascore.enrichment.ner import RuleBasedNer, normalize_name
from atlascore.enrichment.topics import extract_topics
from atlascore.storage.embeddings import chunk_text

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- NER

_SAMPLE = (
    "Apple Inc. (NASDAQ: AAPL) filed its 10-K with the SEC. The Federal Reserve raised rates. "
    "Analysts at Goldman Sachs and the Department of Justice commented. See CIK 0000320193 for "
    "details. $TSLA also rose. Bank of England held steady."
)


def test_ner_extracts_tickers_cik_and_phrases() -> None:
    ner = RuleBasedNer()
    by_name = {c.normalized_name: c for c in ner.extract(_SAMPLE)}

    # Tickers (exchange-qualified + cashtag) carry the structured identifier.
    assert by_name["aapl"].identifiers == {"ticker": "AAPL"}
    assert by_name["aapl"].entity_type == "company"
    assert by_name["tsla"].identifiers == {"ticker": "TSLA"}

    # CIK zero-padded to 10 digits.
    assert by_name["cik:0000320193"].identifiers == {"cik": "0000320193"}

    # Corporate-suffix + government-keyword classification, sentence-bounded (no cross-merge).
    assert by_name["apple inc"].entity_type == "company"
    assert by_name["federal reserve"].entity_type == "organization"
    assert by_name["department of justice"].entity_type == "organization"
    assert "goldman sachs" in by_name  # distinct from the DoJ — "and"/"the" are not connectors

    # Noise control: no single-word generic proper nouns, no cross-sentence merge.
    assert "analysts" not in by_name
    assert not any("federal reserve" in n and n != "federal reserve" for n in by_name)


def test_ner_deterministic() -> None:
    ner = RuleBasedNer()
    assert ner.extract(_SAMPLE) == ner.extract(_SAMPLE)


def test_ner_empty() -> None:
    assert RuleBasedNer().extract("") == []
    assert RuleBasedNer().extract("the quick brown fox") == []  # no capitalized phrases


def test_normalize_name() -> None:
    assert normalize_name("  Apple  Inc. ") == "apple inc"
    assert normalize_name("Apple's") == "apple"


# --------------------------------------------------------------------------- topics


def test_extract_topics() -> None:
    assert extract_topics("CPI inflation rose; the Federal Reserve held rates") == [
        "macroeconomics"
    ]
    assert extract_topics("A remote code execution vulnerability (CVE) needs a patch") == [
        "cybersecurity"
    ]
    assert extract_topics("Quarterly earnings and revenue beat guidance", title="10-K filing") == [
        "finance"
    ]
    # Whole-word match: "rate" must not fire inside "accelerate".
    assert "macroeconomics" not in extract_topics("the project will accelerate next year")
    assert extract_topics("") == []


# --------------------------------------------------------------------------- language


def test_detect_language_prefers_fallback() -> None:
    assert detect_language("anything", fallback="de") == "de"


def test_detect_language_heuristic_and_unknown() -> None:
    assert detect_language("the report is based on the data and it has the figures") == "en"
    assert detect_language("xyz") is None  # too short -> honest unknown, not a guess


# --------------------------------------------------------------------------- embeddings


def test_hashing_embedder_deterministic_and_normalized() -> None:
    backend = HashingEmbeddingBackend(dim=16)
    [v1] = backend.embed(["Apple raised guidance"])
    [v2] = backend.embed(["Apple raised guidance"])
    assert v1 == v2  # deterministic across calls
    assert len(v1) == 16
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


def test_hashing_embedder_empty_is_zero_vector() -> None:
    backend = HashingEmbeddingBackend(dim=8)
    [v] = backend.embed([""])
    assert v == [0.0] * 8


def test_hashing_embedder_distinguishes_text() -> None:
    backend = HashingEmbeddingBackend(dim=64)
    a, b = backend.embed(["inflation rose sharply", "semiconductor chips shipped"])
    assert a != b


def test_embedding_factory_local_default() -> None:
    backend = get_embedding_backend()
    assert backend.model_name == "hashing"
    assert backend.dim == 384  # EMBEDDING_DIM default


def test_chunk_text_bounds() -> None:
    assert chunk_text("") == []
    assert chunk_text("one two three") == ["one two three"]
    long = " ".join(["word"] * 1000)
    chunks = chunk_text(long, max_chars=50, max_chunks=5)
    assert len(chunks) == 5  # capped
    assert all(len(c) <= 50 for c in chunks)
