"""Main document processing LangGraph workflow.

Orchestrates: ingest -> parallel OCR (Marker + Azure DI + Docling) -> merge ->
confidence routing -> HITL review (if needed) -> store results.

Uses native WebSocket streaming (no Redis), PostgresSaver for checkpointing,
and interrupt()/Command(resume=...) for HITL.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from app.config.container import get_container
from app.config.settings import get_settings
from app.workflow.nodes import (
    ingest_document,
    merge_ocr_results,
    run_azure_di_ocr,
    run_marker_ocr,
    run_quality_scoring,
)
from app.workflow.state import DocumentState

logger = logging.getLogger(__name__)


async def route_after_ingest(state: DocumentState) -> list[Send]:
    """Fan out to parallel OCR engines after ingestion."""
    if state.get("status") == "error":
        return [Send("handle_error", state)]

    return [
        Send("run_marker_ocr", state),
        Send("run_azure_di_ocr", state),
        Send("run_quality_scoring", state),
    ]


def route_by_confidence(state: DocumentState) -> str:
    """Route based on composite confidence scores."""
    settings = get_settings()
    scores = state.get("confidence_scores", {})

    if not scores:
        return "hitl_review"

    min_score = min(scores.values()) if scores else 0.0

    if min_score < settings.hitl.review_threshold:
        return "hitl_review"
    return "auto_approve"


async def hitl_review(state: DocumentState) -> dict:
    """Pause workflow for human review of low-confidence pages."""
    settings = get_settings()
    scores = state.get("confidence_scores", {})

    pages_for_review = [
        {
            "page_num": page_num,
            "confidence": score,
            "extraction": next(
                (e for e in state.get("extractions", []) if e["page_num"] == page_num),
                None,
            ),
        }
        for page_num, score in scores.items()
        if score < settings.hitl.auto_approve_threshold
    ]

    pages_for_review.sort(key=lambda p: p["confidence"])

    container = get_container()
    await container.notification.send_update(
        state["doc_id"],
        {
            "type": "hitl_required",
            "pages_count": len(pages_for_review),
            "pages": [p["page_num"] for p in pages_for_review],
        },
    )

    feedback = interrupt(
        {
            "action": "review_required",
            "pages_for_review": pages_for_review,
            "quality_scores": state.get("quality_scores", {}),
        }
    )

    return {
        "hitl_decisions": [feedback] if isinstance(feedback, dict) else feedback,
        "status": "reviewed",
    }


async def auto_approve(state: DocumentState) -> dict:
    """Auto-approve all pages when confidence is above threshold."""
    container = get_container()
    await container.notification.send_update(
        state["doc_id"],
        {"type": "status", "status": "auto_approved"},
    )
    return {"status": "approved"}


async def store_results(state: DocumentState) -> dict:
    """Persist final extraction results."""
    container = get_container()

    from app.core.models.document import DigitalDocument, DocumentMetadata

    doc = DigitalDocument(
        doc_id=state["doc_id"],
        metadata=DocumentMetadata(
            filename=state.get("filename", "unknown"),
            total_pages=state.get("total_pages", 0),
        ),
        raw_markdown=state.get("raw_markdown", {}),
    )

    await container.document_store.save_document(doc)
    await container.notification.send_update(
        state["doc_id"],
        {"type": "status", "status": "completed"},
    )

    return {"status": "completed"}


async def handle_error(state: DocumentState) -> dict:
    """Handle errors in the pipeline."""
    container = get_container()
    await container.notification.send_update(
        state["doc_id"],
        {"type": "error", "error": state.get("error", "Unknown error")},
    )
    return {"status": "error"}


def build_document_graph(checkpointer=None):
    """Construct the document processing LangGraph."""
    builder = StateGraph(DocumentState)

    builder.add_node("ingest_document", ingest_document)
    builder.add_node("run_marker_ocr", run_marker_ocr)
    builder.add_node("run_azure_di_ocr", run_azure_di_ocr)
    builder.add_node("run_quality_scoring", run_quality_scoring)
    builder.add_node("merge_ocr_results", merge_ocr_results)
    builder.add_node("hitl_review", hitl_review)
    builder.add_node("auto_approve", auto_approve)
    builder.add_node("store_results", store_results)
    builder.add_node("handle_error", handle_error)

    builder.add_edge(START, "ingest_document")
    builder.add_conditional_edges("ingest_document", route_after_ingest)

    builder.add_edge("run_marker_ocr", "merge_ocr_results")
    builder.add_edge("run_azure_di_ocr", "merge_ocr_results")
    builder.add_edge("run_quality_scoring", "merge_ocr_results")

    builder.add_conditional_edges("merge_ocr_results", route_by_confidence)
    builder.add_edge("hitl_review", "store_results")
    builder.add_edge("auto_approve", "store_results")
    builder.add_edge("store_results", END)
    builder.add_edge("handle_error", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)
