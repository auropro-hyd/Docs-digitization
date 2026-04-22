"""Tests for the hybrid classifier."""

from __future__ import annotations

from app.bmr.ingest.classifier import (
    HybridClassifier,
    HybridClassifierConfig,
)
from app.bmr.ingest.manifest import Manifest
from app.bmr.ingest.models import ClassificationDecisionSource


def _make_extractor(text_by_filename: dict[str, str]):
    def _extract(content: bytes, filename: str) -> str:
        del content
        return text_by_filename.get(filename, "")

    return _extract


# ── filename tier ────────────────────────────────────────────────────────────


def test_filename_matching_wins(pilot_manifest: Manifest):
    clf = HybridClassifier(pilot_manifest, header_text_extractor=_make_extractor({}))
    outcome = clf.classify_file(
        filename="ProjectX_BPCR_batch42.pdf", content=b"irrelevant"
    )
    assert outcome.role == "BPCR"
    assert outcome.confidence > 0.4
    assert outcome.decision_source == ClassificationDecisionSource.FILENAME


def test_unknown_filename_with_no_header_yields_no_role(pilot_manifest: Manifest):
    clf = HybridClassifier(pilot_manifest, header_text_extractor=_make_extractor({}))
    outcome = clf.classify_file(filename="random.pdf", content=b"")
    assert outcome.role is None
    assert outcome.decision_source == ClassificationDecisionSource.UNKNOWN


# ── header tier ──────────────────────────────────────────────────────────────


def test_header_text_classifies_when_filename_unhelpful(pilot_manifest: Manifest):
    extractor = _make_extractor(
        {"doc1.pdf": "BATCH MANUFACTURING RECORD  Product Master Batch Record"}
    )
    clf = HybridClassifier(pilot_manifest, header_text_extractor=extractor)
    outcome = clf.classify_file(filename="doc1.pdf", content=b"x")
    assert outcome.role == "BMR"
    assert outcome.decision_source == ClassificationDecisionSource.HEADER


def test_header_case_insensitive(pilot_manifest: Manifest):
    extractor = _make_extractor(
        {"doc.pdf": "raw material dispensing record material issue"}
    )
    clf = HybridClassifier(pilot_manifest, header_text_extractor=extractor)
    outcome = clf.classify_file(filename="doc.pdf", content=b"x")
    assert outcome.role == "RawMaterialPage"


# ── ambiguity + threshold ────────────────────────────────────────────────────


def test_ambiguous_header_returns_no_role(pilot_manifest: Manifest):
    extractor = _make_extractor(
        {"doc.pdf": "batch production record raw material"}
    )
    # Both BPCR and RawMaterialPage get a single header keyword hit => tied.
    clf = HybridClassifier(
        pilot_manifest,
        config=HybridClassifierConfig(
            confidence_margin=0.1, min_confidence=0.1, header_saturation_hits=1
        ),
        header_text_extractor=extractor,
    )
    outcome = clf.classify_file(filename="doc.pdf", content=b"x")
    assert outcome.role is None
    assert any("ambiguous" in n for n in outcome.notes)


def test_filename_and_header_reinforce_each_other(pilot_manifest: Manifest):
    extractor = _make_extractor(
        {
            "bmr_batch42.pdf": (
                "BATCH MANUFACTURING RECORD Product Master Batch Record"
            )
        }
    )
    clf = HybridClassifier(pilot_manifest, header_text_extractor=extractor)
    outcome = clf.classify_file(filename="bmr_batch42.pdf", content=b"x")
    assert outcome.role == "BMR"
    # filename alone is 1.0, header contributes further => confidence high
    assert outcome.confidence > 0.6


def test_extractor_failure_is_captured_as_note(pilot_manifest: Manifest):
    def _raise(_c, _f):
        raise RuntimeError("boom")

    clf = HybridClassifier(pilot_manifest, header_text_extractor=_raise)
    outcome = clf.classify_file(filename="bpcr_batch42.pdf", content=b"x")
    assert outcome.role == "BPCR"  # filename tier still succeeds
    assert any("header_extractor_failed" in n for n in outcome.notes)
