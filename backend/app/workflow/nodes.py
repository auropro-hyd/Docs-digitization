"""LangGraph workflow node functions.

Each function is a node in the document processing graph. Nodes use injected
ports (via the DI container) -- never concrete adapters directly.

Two merge paths exist:
  merge_azure_di_results: confidence comes from Azure DI's per-word scores
  merge_marker_results:   confidence comes from Docling quality scores
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config.container import get_container
from app.config.settings import get_settings
from app.workflow.state import DocumentState

logger = logging.getLogger(__name__)


async def ingest_document(state: DocumentState) -> dict:
    """Initial node: validate the uploaded PDF and determine page count."""
    pdf_path = Path(state["pdf_path"])
    if not pdf_path.exists():
        return {"status": "error", "error": f"PDF not found: {pdf_path}"}

    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        logger.warning(f"Could not determine page count for {pdf_path}: {e}")
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


# ═══════════════════════════════════════════════════════════════
#  Azure DI Flow
# ═══════════════════════════════════════════════════════════════


async def run_azure_di_ocr(state: DocumentState) -> dict:
    """Run Azure Document Intelligence on the full document."""
    import asyncio

    container = get_container()
    doc_id = state["doc_id"]
    await container.notification.send_update(doc_id, {"type": "status", "status": "azure_di_running"})

    loop = asyncio.get_event_loop()

    def _on_ocr_progress(percent: int, label: str) -> None:
        """Thread-safe bridge: schedule the async WS broadcast from the executor thread."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                container.notification.send_update(doc_id, {
                    "type": "progress",
                    "status": "azure_di_running",
                    "percent": percent,
                    "label": label,
                }),
                loop,
            )
            future.result(timeout=2)
        except Exception:
            pass

    try:
        from app.core.services.selection_semantics import summarize_selection_semantics

        result = await container.ocr_engine.extract(
            state["pdf_path"],
            progress_callback=_on_ocr_progress,
        )
        azure_results: dict = {}
        raw_markdown: dict = {}

        for page in result.pages:
            word_confidences = [w.confidence for w in page.words]
            handwritten_words = [w for w in page.words if w.is_handwritten]

            azure_results[page.page_num] = {
                "markdown": page.markdown,
                "page_width": page.page_width,
                "page_height": page.page_height,
                "page_unit": page.page_unit,
                "parser_repairs": page.parser_repairs,
                "parser_repair_count": len(page.parser_repairs),
                "word_count": len(page.words),
                "avg_confidence": sum(word_confidences) / len(word_confidences) if word_confidences else 0.0,
                "min_confidence": min(word_confidences) if word_confidences else 0.0,
                "handwritten_count": len(handwritten_words),
                "barcodes": [b.model_dump() for b in page.barcodes],
                "selection_marks": [s.model_dump() for s in page.selection_marks],
                "word_confidences": word_confidences,
                "selection_semantics": summarize_selection_semantics(
                    page.markdown,
                    [s.model_dump() for s in page.selection_marks],
                ),
            }
            raw_markdown[page.page_num] = page.markdown

        if result.full_markdown:
            raw_markdown["full"] = result.full_markdown

        return {
            "azure_di_results": azure_results,
            "raw_markdown": raw_markdown,
            "table_metadata": result.table_metadata,
            "key_value_pairs": [kv.model_dump() for kv in result.key_value_pairs],
            "styles": [s.model_dump() for s in result.styles],
            "signatures": [s.model_dump() for s in result.signatures],
            "languages": [l.model_dump() for l in result.languages],
            "status": "azure_di_complete",
        }
    except Exception as e:
        logger.exception("Azure DI OCR failed")
        return {"status": "error", "error": f"OCR extraction failed: {e}"}


async def merge_azure_di_results(state: DocumentState) -> dict:
    """Build extractions and confidence scores from Azure DI output.

    In Azure DI mode, confidence comes directly from DI's per-word scores +
    validation rules. No Docling or Marker needed.
    """
    container = get_container()

    from app.core.services.validation_rules import validate_page_extraction

    extractions: list = []
    confidence_scores: dict = {}

    azure = state.get("azure_di_results", {})
    table_metadata = state.get("table_metadata", [])
    kv_pairs = state.get("key_value_pairs", [])
    signatures = state.get("signatures", [])
    total_pages = state.get("total_pages", 0)

    # Fall back to actual Azure DI result keys if total_pages is 0
    if total_pages == 0 and azure:
        page_nums = sorted(int(k) for k in azure if isinstance(k, int) or (isinstance(k, str) and k.isdigit()))
        if page_nums:
            total_pages = max(page_nums)
            logger.info(f"total_pages was 0, inferred {total_pages} from Azure DI results")

    await container.notification.send_update(state["doc_id"], {
        "type": "status",
        "status": "merging_results",
        "total_pages": total_pages,
    })

    for page_num in range(1, total_pages + 1):
        page_str = str(page_num)
        azure_page = azure.get(page_num, azure.get(page_str, {}))

        page_tables = [
            t for t in table_metadata if page_num in t.get("page_numbers", [])
        ]
        page_kv = [kv for kv in kv_pairs if kv.get("page_num") == page_num]
        page_sigs = [s for s in signatures if s.get("page_num") == page_num]

        for i, t in enumerate(page_tables):
            t["component_id"] = f"p{page_num}-tbl-{i}"
        for i, kv in enumerate(page_kv):
            kv["component_id"] = f"p{page_num}-kv-{i}"
        for i, sig in enumerate(page_sigs):
            sig["component_id"] = f"p{page_num}-sig-{i}"

        extraction = {
            "page_num": page_num,
            "markdown": azure_page.get("markdown", ""),
            "page_width": azure_page.get("page_width"),
            "page_height": azure_page.get("page_height"),
            "page_unit": azure_page.get("page_unit"),
            "parser_repairs": azure_page.get("parser_repairs", []),
            "parser_repair_count": azure_page.get("parser_repair_count", 0),
            "handwritten_count": azure_page.get("handwritten_count", 0),
            "barcodes": azure_page.get("barcodes", []),
            "selection_marks": azure_page.get("selection_marks", []),
            "selection_semantics": azure_page.get("selection_semantics", {}),
            "tables": page_tables,
            "key_value_pairs": page_kv,
            "signatures": page_sigs,
            "content_component_id": f"p{page_num}-content",
        }
        extractions.append(extraction)

        # Confidence from Azure DI word-level scores + validation
        word_confidences = azure_page.get("word_confidences", [])
        avg_conf = sum(word_confidences) / len(word_confidences) if word_confidences else 0.5
        min_conf = min(word_confidences) if word_confidences else 0.5

        validation = validate_page_extraction(extraction)

        # Weighted: 50% Azure DI avg confidence + 20% min word confidence + 30% validation
        confidence = 0.50 * avg_conf + 0.20 * min_conf + 0.30 * validation.pass_rate
        confidence_scores[page_num] = round(min(max(confidence, 0.0), 1.0), 3)

    settings = get_settings()
    for page_num, score in confidence_scores.items():
        await container.notification.send_update(state["doc_id"], {
            "type": "page_update",
            "page_num": page_num,
            "confidence": score,
            "status": "approved" if score >= settings.hitl.auto_approve_threshold else "needs_review",
        })

    return {
        "extractions": extractions,
        "confidence_scores": confidence_scores,
        "total_pages": total_pages,
        "status": "merged",
    }


# ═══════════════════════════════════════════════════════════════
#  Marker + Docling Flow
# ═══════════════════════════════════════════════════════════════


async def run_marker_ocr(state: DocumentState) -> dict:
    """Run Marker OCR on the full document."""
    container = get_container()
    await container.notification.send_update(state["doc_id"], {"type": "status", "status": "marker_ocr_running"})

    try:
        result = await container.ocr_engine.extract(state["pdf_path"])
        marker_results: dict = {}
        raw_markdown: dict = {}

        for page in result.pages:
            marker_results[page.page_num] = {
                "markdown": page.markdown,
                "word_count": len(page.words),
                "images": list(page.images.keys()),
            }
            raw_markdown[page.page_num] = page.markdown

        return {
            "marker_results": marker_results,
            "raw_markdown": raw_markdown,
            "status": "marker_complete",
        }
    except Exception as e:
        logger.exception("Marker OCR failed")
        return {"status": "error", "error": f"OCR extraction failed: {e}"}


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


async def merge_marker_results(state: DocumentState) -> dict:
    """Build extractions and confidence from Marker + Docling output.

    In marker_docling mode, confidence comes from Docling quality scores +
    validation rules. No Azure DI involved.
    """
    container = get_container()

    from app.core.services.validation_rules import validate_page_extraction

    extractions: list = []
    confidence_scores: dict = {}

    marker = state.get("marker_results", {})
    quality = state.get("quality_scores", {})
    per_page_quality = quality.get("per_page", {})
    total_pages = state.get("total_pages", 0)

    # Fall back to actual Marker result keys if total_pages is 0
    if total_pages == 0 and marker:
        page_nums = sorted(int(k) for k in marker if isinstance(k, int) or (isinstance(k, str) and k.isdigit()))
        if page_nums:
            total_pages = max(page_nums)
            logger.info(f"total_pages was 0, inferred {total_pages} from Marker results")

    await container.notification.send_update(state["doc_id"], {
        "type": "status",
        "status": "merging_results",
        "total_pages": total_pages,
    })

    for page_num in range(1, total_pages + 1):
        page_str = str(page_num)
        marker_page = marker.get(page_num, marker.get(page_str, {}))
        quality_page = per_page_quality.get(page_num, per_page_quality.get(page_str, {}))

        extraction = {
            "page_num": page_num,
            "markdown": marker_page.get("markdown", ""),
            "handwritten_count": 0,
            "barcodes": [],
            "selection_marks": [],
        }
        extractions.append(extraction)

        # Confidence from Docling quality + validation
        quality_mean = 0.5
        if quality_page:
            scores = [
                quality_page.get("layout_score", 0.5),
                quality_page.get("table_score", 0.5),
                quality_page.get("ocr_score", 0.5),
                quality_page.get("parse_score", 0.5),
            ]
            quality_mean = sum(scores) / len(scores)

        validation = validate_page_extraction(extraction)

        # Weighted: 60% Docling quality + 40% validation
        confidence = 0.60 * quality_mean + 0.40 * validation.pass_rate
        confidence_scores[page_num] = round(min(max(confidence, 0.0), 1.0), 3)

    settings = get_settings()
    for page_num, score in confidence_scores.items():
        await container.notification.send_update(state["doc_id"], {
            "type": "page_update",
            "page_num": page_num,
            "confidence": score,
            "status": "approved" if score >= settings.hitl.auto_approve_threshold else "needs_review",
        })

    return {
        "extractions": extractions,
        "confidence_scores": confidence_scores,
        "total_pages": total_pages,
        "status": "merged",
    }
