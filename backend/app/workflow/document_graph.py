"""Main document processing LangGraph workflow.

Supports two pipeline modes:
  azure_di:       ingest -> Azure DI OCR -> confidence from DI -> HITL -> store
  marker_docling: ingest -> Marker OCR + Docling scoring -> confidence -> HITL -> store

Uses native WebSocket streaming, MemorySaver for checkpointing,
and interrupt()/Command(resume=...) for HITL.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.config.container import get_container
from app.config.settings import get_settings
from app.workflow.nodes import (
    ingest_document,
    merge_azure_di_results,
    merge_marker_results,
    run_azure_di_ocr,
    run_marker_ocr,
    run_quality_scoring,
)
from app.workflow.state import DocumentState

logger = logging.getLogger(__name__)


def route_after_ingest(state: DocumentState) -> str:
    """Route to the correct OCR flow based on pipeline mode."""
    if state.get("status") == "error":
        return "handle_error"

    settings = get_settings()
    mode = settings.pipeline.mode

    if mode == "marker_docling":
        return "run_marker_ocr"
    return "run_azure_di_ocr"


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
    from langgraph.types import interrupt

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

    # Filter raw_markdown to only integer page keys (exclude "full" convenience key)
    raw_md = {k: v for k, v in state.get("raw_markdown", {}).items() if isinstance(k, int)}
    doc = DigitalDocument(
        doc_id=state["doc_id"],
        metadata=DocumentMetadata(
            filename=state.get("filename", "unknown"),
            total_pages=state.get("total_pages", 0),
        ),
        raw_markdown=raw_md,
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
    """Construct the document processing LangGraph.

    Graph topology depends on pipeline mode:

      azure_di mode:
        START -> ingest -> run_azure_di_ocr -> merge_azure_di -> confidence -> ...

      marker_docling mode:
        START -> ingest -> run_marker_ocr -> run_quality_scoring -> merge_marker -> confidence -> ...
    """
    builder = StateGraph(DocumentState)

    # Common nodes
    builder.add_node("ingest_document", ingest_document)
    builder.add_node("hitl_review", hitl_review)
    builder.add_node("auto_approve", auto_approve)
    builder.add_node("store_results", store_results)
    builder.add_node("handle_error", handle_error)

    # Azure DI flow nodes
    builder.add_node("run_azure_di_ocr", run_azure_di_ocr)
    builder.add_node("merge_azure_di_results", merge_azure_di_results)

    # Marker + Docling flow nodes
    builder.add_node("run_marker_ocr", run_marker_ocr)
    builder.add_node("run_quality_scoring", run_quality_scoring)
    builder.add_node("merge_marker_results", merge_marker_results)

    # Entry
    builder.add_edge(START, "ingest_document")
    builder.add_conditional_edges("ingest_document", route_after_ingest)

    # Azure DI flow
    builder.add_conditional_edges(
        "run_azure_di_ocr",
        lambda state: "handle_error" if state.get("status") == "error" else "merge_azure_di_results",
    )
    builder.add_conditional_edges("merge_azure_di_results", route_by_confidence)

    # Marker + Docling flow
    builder.add_conditional_edges(
        "run_marker_ocr",
        lambda state: "handle_error" if state.get("status") == "error" else "run_quality_scoring",
    )
    builder.add_edge("run_quality_scoring", "merge_marker_results")
    builder.add_conditional_edges("merge_marker_results", route_by_confidence)

    # Common tail
    builder.add_edge("hitl_review", "store_results")
    builder.add_edge("auto_approve", "store_results")
    builder.add_edge("store_results", END)
    builder.add_edge("handle_error", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)
