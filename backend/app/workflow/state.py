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
    """Main document processing workflow state."""

    doc_id: str
    pdf_path: str
    filename: str
    total_pages: int

    marker_results: Annotated[dict, _merge_dicts]
    azure_di_results: Annotated[dict, _merge_dicts]
    quality_scores: dict

    extractions: Annotated[list, _merge_lists]
    confidence_scores: Annotated[dict, _merge_dicts]
    page_classifications: Annotated[dict, _merge_dicts]

    sections: list
    raw_markdown: Annotated[dict, _merge_dicts]

    hitl_pages_for_review: list
    hitl_decisions: Annotated[list, _merge_lists]

    status: str
    error: str | None


class ComplianceState(TypedDict):
    """Compliance review subgraph state."""

    doc_id: str
    extractions: list
    sections: list

    alcoa_findings: Annotated[list, _merge_lists]
    gmp_findings: Annotated[list, _merge_lists]
    checklist_findings: Annotated[list, _merge_lists]
    sop_findings: Annotated[list, _merge_lists]
    aggregated_findings: list
    compliance_score: float | None
