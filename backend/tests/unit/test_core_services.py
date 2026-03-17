"""Unit tests for core domain services.

These tests use NO external dependencies (no Marker, Azure DI, Ollama, etc.)
-- only pure domain logic with mock data.
"""

from app.config.settings import HITLConfig
from app.core.models.quality import PageQualityScore
from app.core.services.confidence import (
    CompositeConfidenceScorer,
    CompositeConfidenceWeights,
    ValidationResults,
)
from app.core.services.hitl_router import HITLRouter
from app.core.services.section_builder import SectionBuilder
from app.core.services.validation_rules import validate_page_extraction


class TestCompositeConfidenceScorer:
    def test_high_confidence_all_good(self):
        scorer = CompositeConfidenceScorer()
        score = scorer.score_page(
            docling_page=PageQualityScore(
                page_num=1, layout_score=0.95, table_score=0.9, ocr_score=0.92, parse_score=0.93
            ),
            azure_di_word_confidences=[0.98, 0.95, 0.97],
            marker_table_score=5,
            validation_results=ValidationResults(rules_checked=3, rules_passed=3),
        )
        assert score > 0.9

    def test_low_confidence_poor_quality(self):
        scorer = CompositeConfidenceScorer()
        score = scorer.score_page(
            docling_page=PageQualityScore(
                page_num=1, layout_score=0.2, table_score=0.1, ocr_score=0.15, parse_score=0.2
            ),
            azure_di_word_confidences=[0.3, 0.1, 0.2],
            marker_table_score=1,
            validation_results=ValidationResults(rules_checked=3, rules_passed=0),
        )
        assert score < 0.3

    def test_defaults_when_no_data(self):
        scorer = CompositeConfidenceScorer()
        score = scorer.score_page()
        assert 0.0 <= score <= 1.0

    def test_classify_tiers(self):
        scorer = CompositeConfidenceScorer()
        assert scorer.classify_confidence(0.95) == "high"
        assert scorer.classify_confidence(0.85) == "medium"
        assert scorer.classify_confidence(0.5) == "low"

    def test_custom_weights(self):
        weights = CompositeConfidenceWeights(docling_mean=0.5, azure_di_min_word=0.2, marker_table=0.1, validation=0.2)
        scorer = CompositeConfidenceScorer(weights)
        score = scorer.score_page(
            docling_page=PageQualityScore(
                page_num=1, layout_score=1.0, table_score=1.0, ocr_score=1.0, parse_score=1.0
            ),
        )
        assert score > 0.5


class TestHITLRouter:
    def test_all_high_confidence_auto_approved(self):
        router = HITLRouter(HITLConfig())
        scores = {1: 0.95, 2: 0.92, 3: 0.98}
        decisions = router.route_pages(scores)
        assert all(d.action == "auto_approve" for d in decisions)
        assert not router.needs_human_review(scores)

    def test_low_confidence_triggers_review(self):
        router = HITLRouter(HITLConfig())
        scores = {1: 0.95, 2: 0.4, 3: 0.98}
        decisions = router.route_pages(scores)
        review_pages = [d for d in decisions if d.action == "review_required"]
        assert len(review_pages) == 1
        assert review_pages[0].page_num == 2
        assert router.needs_human_review(scores)

    def test_medium_confidence_batch_review(self):
        router = HITLRouter(HITLConfig(batch_review_enabled=True))
        scores = {1: 0.95, 2: 0.75, 3: 0.98}
        decisions = router.route_pages(scores)
        batch = [d for d in decisions if d.action == "batch_review"]
        assert len(batch) == 1
        assert batch[0].page_num == 2

    def test_review_summary(self):
        router = HITLRouter(HITLConfig())
        scores = {1: 0.95, 2: 0.4, 3: 0.75, 4: 0.98}
        summary = router.get_review_summary(scores)
        assert summary["total_pages"] == 4
        assert summary["auto_approved"] == 2
        assert summary["needs_review"] == 1
        assert summary["batch_review"] == 1


class TestSectionBuilder:
    def test_detect_sections_from_headings(self):
        builder = SectionBuilder()
        pages = {
            1: "# Introduction\nSome intro text",
            2: "More text without heading",
            3: "# Manufacturing\nStep 1...",
            4: "Step 2...",
        }
        sections, headers = builder.build_structure(pages)
        assert len(sections) == 2
        assert sections[0].name == "Introduction"
        assert sections[0].start_page == 1
        assert sections[0].end_page == 2
        assert sections[1].name == "Manufacturing"

    def test_no_headings_single_section(self):
        builder = SectionBuilder()
        pages = {1: "Text only", 2: "More text", 3: "Even more text"}
        sections, _ = builder.build_structure(pages)
        assert len(sections) == 1
        assert sections[0].section_type == "full_document"

    def test_repeating_headers_detected(self):
        builder = SectionBuilder()
        pages = {
            1: "Batch Record 2538\nContent...",
            2: "Batch Record 2538\nMore content...",
            3: "Batch Record 2538\nStill more...",
        }
        headers = builder.detect_repeating_headers(pages)
        assert len(headers) >= 1
        assert headers[0].text == "Batch Record 2538"


class TestValidationRules:
    def test_valid_content_passes(self):
        result = validate_page_extraction({"markdown": "Batch: 2538104192 Date: 15/03/2025 Qty: 4305 kg"})
        assert result.pass_rate > 0.5

    def test_empty_content_fails(self):
        result = validate_page_extraction({"markdown": ""})
        assert result.pass_rate < 1.0
        assert any("too short" in f for f in result.failures)

    def test_implausible_date_detected(self):
        result = validate_page_extraction({"markdown": "Some content here\nDate: 01/01/1950\nMore content follows"})
        has_date_failure = any("Implausible date" in f for f in result.failures)
        assert has_date_failure
