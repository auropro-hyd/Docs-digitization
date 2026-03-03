"""LangGraph workflow node functions.

Each function is a node in the document processing graph. Nodes use injected
ports (via the DI container) -- never concrete adapters directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config.container import get_container
from app.workflow.state import DocumentState

logger = logging.getLogger(__name__)


async def ingest_document(state: DocumentState) -> dict:
    """Initial node: validate the uploaded PDF and determine page count."""
    pdf_path = Path(state["pdf_path"])
    if not pdf_path.exists():
        return {"status": "error", "error": f"PDF not found: {pdf_path}"}

    try:
        import fitz  # PyMuPDF, bundled with marker-pdf

        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        doc.close()
    except Exception:
        total_pages = 0

    container = get_container()
    await container.notification.send_update(
        state["doc_id"],
        {"type": "status", "status": "ingested", "total_pages": total_pages},
    )

    return {
        "total_pages": total_pages,
        "status": "ingested",
    }


async def run_marker_ocr(state: DocumentState) -> dict:
    """Run Marker OCR on the full document."""
    container = get_container()
    await container.notification.send_update(state["doc_id"], {"type": "status", "status": "marker_ocr_running"})

    try:
        result = await container.primary_ocr.extract(state["pdf_path"])
        marker_results: dict = {}
        raw_markdown: dict = {}

        for page in result.pages:
            marker_results[page.page_num] = {
                "markdown": page.markdown,
                "word_count": len(page.words),
            }
            raw_markdown[page.page_num] = page.markdown

        return {
            "marker_results": marker_results,
            "raw_markdown": raw_markdown,
            "status": "marker_complete",
        }
    except Exception as e:
        logger.exception("Marker OCR failed")
        return {"status": "marker_error", "error": str(e)}


async def run_azure_di_ocr(state: DocumentState) -> dict:
    """Run Azure DI on the full document for handwriting, barcodes, confidence."""
    container = get_container()
    await container.notification.send_update(state["doc_id"], {"type": "status", "status": "azure_di_running"})

    try:
        result = await container.secondary_ocr.extract(state["pdf_path"])
        azure_results: dict = {}

        for page in result.pages:
            word_confidences = [w.confidence for w in page.words]
            handwritten_words = [w for w in page.words if w.is_handwritten]

            azure_results[page.page_num] = {
                "word_count": len(page.words),
                "avg_confidence": sum(word_confidences) / len(word_confidences) if word_confidences else 0.0,
                "min_confidence": min(word_confidences) if word_confidences else 0.0,
                "handwritten_count": len(handwritten_words),
                "barcodes": [b.model_dump() for b in page.barcodes],
                "selection_marks": [s.model_dump() for s in page.selection_marks],
                "word_confidences": word_confidences,
            }

        return {"azure_di_results": azure_results, "status": "azure_di_complete"}
    except Exception as e:
        logger.exception("Azure DI OCR failed")
        return {"status": "azure_di_error", "error": str(e)}


async def run_quality_scoring(state: DocumentState) -> dict:
    """Run Docling quality scoring on the document."""
    container = get_container()
    await container.notification.send_update(state["doc_id"], {"type": "status", "status": "quality_scoring"})

    try:
        report = await container.quality_scorer.score(state["pdf_path"])
        return {
            "quality_scores": report.model_dump(),
            "status": "quality_scored",
        }
    except Exception as e:
        logger.exception("Quality scoring failed")
        return {"quality_scores": {}, "status": "quality_error", "error": str(e)}


async def merge_ocr_results(state: DocumentState) -> dict:
    """Merge results from all OCR engines into a unified view."""
    await get_container().notification.send_update(state["doc_id"], {"type": "status", "status": "merging_results"})

    extractions: list = []
    confidence_scores: dict = {}

    marker = state.get("marker_results", {})
    azure = state.get("azure_di_results", {})
    quality = state.get("quality_scores", {})
    per_page_quality = quality.get("per_page", {})

    total_pages = state.get("total_pages", 0)
    for page_num in range(1, total_pages + 1):
        page_str = str(page_num)
        marker_page = marker.get(page_num, marker.get(page_str, {}))
        azure_page = azure.get(page_num, azure.get(page_str, {}))
        quality_page = per_page_quality.get(page_num, per_page_quality.get(page_str, {}))

        extraction = {
            "page_num": page_num,
            "markdown": marker_page.get("markdown", ""),
            "handwritten_count": azure_page.get("handwritten_count", 0),
            "barcodes": azure_page.get("barcodes", []),
            "selection_marks": azure_page.get("selection_marks", []),
        }
        extractions.append(extraction)

        confidence_scores[page_num] = _compute_page_confidence(quality_page, azure_page, marker_page)

    return {
        "extractions": extractions,
        "confidence_scores": confidence_scores,
        "status": "merged",
    }


def _compute_page_confidence(
    quality_page: dict,
    azure_page: dict,
    marker_page: dict,
) -> float:
    """Composite confidence score from multiple sources."""
    weights = {
        "docling_mean": 0.30,
        "azure_di_min_word": 0.25,
        "marker_table": 0.15,
        "validation": 0.30,
    }

    quality_mean = 0.5
    if quality_page:
        scores = [
            quality_page.get("layout_score", 0.5),
            quality_page.get("table_score", 0.5),
            quality_page.get("ocr_score", 0.5),
            quality_page.get("parse_score", 0.5),
        ]
        quality_mean = sum(scores) / len(scores)

    azure_min = azure_page.get("min_confidence", 0.5) if azure_page else 0.5
    marker_table = marker_page.get("table_score", 3) if marker_page else 3
    validation_pass = 0.8  # placeholder until custom validation rules are built

    score = 0.0
    score += weights["docling_mean"] * quality_mean
    score += weights["azure_di_min_word"] * azure_min
    score += weights["marker_table"] * (marker_table / 5.0)
    score += weights["validation"] * validation_pass
    return round(min(max(score, 0.0), 1.0), 3)
