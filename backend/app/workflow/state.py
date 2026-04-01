"""LangGraph workflow state definitions.

TypedDict states for the document processing and compliance review graphs.
"""

from __future__ import annotations

from typing import Annotated, TypedDict


def _merge_lists(left: list, right: list) -> list:
    """Reducer that merges lists by appending."""
    return left + right


def _merge_dicts(left: dict, right: dict) -> dict:
    """Reducer that merges dicts by updating."""
    merged = left.copy()
    merged.update(right)
    return merged


class PageProcessingState(TypedDict):
    """State for processing a single page (used with Send)."""

    doc_id: str
    pdf_path: str
    page_num: int


class DocumentState(TypedDict):
    """Main document processing workflow state.

    Pipeline modes populate different subsets of fields:
      azure_di:       azure_di_results, raw_markdown, extractions, confidence_scores
      marker_docling: marker_results, quality_scores, raw_markdown, extractions, confidence_scores

    Common fields: doc_id, pdf_path, filename, total_pages, status, error,
                   hitl_decisions, extractions, confidence_scores, raw_markdown
    """

    # ── Identity ──────────────────────────────────────────────
    doc_id: str
    pdf_path: str
    filename: str
    total_pages: int

    # ── OCR results (mode-specific) ───────────────────────────
    marker_results: Annotated[dict, _merge_dicts]  # marker_docling mode
    azure_di_results: Annotated[dict, _merge_dicts]  # azure_di mode
    quality_scores: dict  # marker_docling mode (Docling)

    # ── Unified extraction output (both modes) ────────────────
    extractions: Annotated[list, _merge_lists]
    confidence_scores: Annotated[dict, _merge_dicts]
    raw_markdown: Annotated[dict, _merge_dicts]
    table_metadata: list
    document_quality: dict

    # ── Enriched metadata (Azure DI features) ─────────────────
    key_value_pairs: list
    styles: list
    signatures: list
    languages: list

    # ── Page intelligence (planned — SectionBuilder, PageClassifier) ──
    page_classifications: Annotated[dict, _merge_dicts]
    sections: list

    # ── HITL ──────────────────────────────────────────────────
    hitl_pages_for_review: list  # Populated by hitl_review node
    hitl_decisions: Annotated[list, _merge_lists]

    # ── Status ────────────────────────────────────────────────
    status: str
    error: str | None


class ComplianceState(TypedDict):
    """Compliance review state (used by the compliance pipeline)."""

    doc_id: str
    filename: str
    total_pages: int
    extractions: list
    key_value_pairs: list
    sections: list

    # Populated by pipeline
    orchestrator_result: dict
    agent_reports: Annotated[list, _merge_lists]
    aggregated_findings: list
    compliance_score: float | None
    report: dict
