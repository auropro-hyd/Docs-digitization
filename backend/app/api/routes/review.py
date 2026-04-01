"""Review API routes — serves real extraction data from pipeline results."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import markdown as md_lib
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.config.settings import get_settings

_PAGE_NUMBER_RE = re.compile(r'<!-- PageNumber="[^"]*" -->\s*')
_EMPTY_FIGURE_RE = re.compile(
    r'<figure>\s*(?:\S[^<]{0,60}\s*)?</figure>\s*\n*',
    re.DOTALL,
)


def _clean_md(md: str) -> str:
    """Lightweight cleanup for markdown served to the frontend."""
    if not md:
        return md
    md = _PAGE_NUMBER_RE.sub("", md)
    md = _EMPTY_FIGURE_RE.sub("", md)
    md = md.replace(":selected:", "\u2611").replace(":unselected:", "\u2610")
    return md.strip()

router = APIRouter()
logger = logging.getLogger(__name__)


class EditPageBody(BaseModel):
    markdown: Optional[str] = None


class FlagPageBody(BaseModel):
    reason: Optional[str] = None


class ComponentActionBody(BaseModel):
    component_id: str
    action: str  # "approve" | "edit" | "flag"
    value: Optional[str] = None
    reason: Optional[str] = None


class BulkComponentActionBody(BaseModel):
    component_ids: list[str]
    action: str  # "approve" | "flag"


def _derive_page_status(
    default_status: str,
    page_num: int,
    page_decisions: list,
    comp_decisions: dict,
    extraction: dict,
) -> str:
    """Derive page status from component-level decisions (if any) or page-level decision."""
    page_decision = next(
        (d for d in page_decisions if isinstance(d, dict) and d.get("page_num") == page_num),
        None,
    )
    if page_decision:
        return page_decision.get("status", default_status)

    page_cids = _get_page_component_ids(extraction)
    if not page_cids:
        return default_status

    statuses = [comp_decisions[cid].get("status") for cid in page_cids if cid in comp_decisions]
    if not statuses:
        return default_status

    if any(s == "flagged" for s in statuses):
        return "flagged"
    if all(s in ("approved", "edited") for s in statuses) and len(statuses) == len(page_cids):
        return "approved"
    return "needs_review"


def _get_page_component_ids(extraction: dict) -> list[str]:
    """Collect all component IDs from an extraction."""
    cids = []
    cid = extraction.get("content_component_id")
    if cid:
        cids.append(cid)
    for item in extraction.get("key_value_pairs", []):
        cid = item.get("component_id")
        if cid:
            cids.append(cid)
    for item in extraction.get("signatures", []):
        cid = item.get("component_id")
        if cid:
            cids.append(cid)
    for item in extraction.get("tables", []):
        cid = item.get("component_id")
        if cid:
            cids.append(cid)
    return cids


def _load_results(doc_id: str) -> dict:
    """Load pipeline results for a document."""
    settings = get_settings()
    result_path = Path(settings.storage.base_path) / doc_id / "result.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail=f"No results for document {doc_id}")
    return json.loads(result_path.read_text())


def _save_results(doc_id: str, data: dict) -> None:
    """Save updated results back to disk."""
    settings = get_settings()
    result_path = Path(settings.storage.base_path) / doc_id / "result.json"
    result_path.write_text(json.dumps(data, indent=2, default=str))


def _lookup_component_value(results: dict, component_id: str) -> str:
    for ext in results.get("extractions", []) or []:
        if ext.get("content_component_id") == component_id:
            return str(ext.get("markdown", "") or "")
        for kv in ext.get("key_value_pairs", []) or []:
            if kv.get("component_id") == component_id:
                return str(kv.get("normalized_value") or kv.get("value") or "")
        for sig in ext.get("signatures", []) or []:
            if sig.get("component_id") == component_id:
                return str(sig.get("status") or "")
    return ""


def _append_correction(results: dict, record: dict) -> None:
    corrections = results.setdefault("review_corrections", [])
    corrections.append(record)
    settings = get_settings()
    from app.core.services.feedback_learning import build_correction_artifacts, evaluate_retraining_trigger

    artifacts = build_correction_artifacts(corrections)
    results["correction_dictionary"] = artifacts.get("correction_dictionary", {})
    results["ocr_confusion_map"] = artifacts.get("ocr_confusion_map", {})
    results["correction_summary"] = artifacts.get("summary", {})
    results["retraining_trigger"] = evaluate_retraining_trigger(
        corrections,
        threshold_correction_rate=settings.azure_di.drift_threshold_correction_rate,
        threshold_critical_rate=settings.azure_di.drift_threshold_critical_error_rate,
        min_corrections_for_trigger=settings.azure_di.drift_min_corrections_for_trigger,
    )


def _page_num_from_component_id(component_id: str) -> int:
    m = re.match(r"^p(\d+)-", str(component_id or ""))
    return int(m.group(1)) if m else 0


def _get_by_page(data: dict, page_num: int, default=None):
    """Look up a value in a dict that may have int or str keys (JSON round-trip)."""
    return data.get(page_num, data.get(str(page_num), default))


@router.get("/{doc_id}/pages")
async def get_review_pages(doc_id: str):
    """Get all pages with their extraction data and confidence scores."""
    results = _load_results(doc_id)

    extractions = results.get("extractions", [])
    confidence_scores = results.get("confidence_scores", {})
    raw_markdown = results.get("raw_markdown", {})
    decisions = results.get("hitl_decisions", [])
    comp_decisions: dict = results.get("component_decisions", {})

    settings = get_settings()
    pages = []
    approved_count = 0
    flagged_count = 0

    for ext in extractions:
        page_num = ext.get("page_num", 0)
        confidence = _get_by_page(confidence_scores, page_num, 0.0)
        markdown = _get_by_page(raw_markdown, page_num, ext.get("markdown", ""))

        if confidence >= settings.hitl.auto_approve_threshold:
            default_status = "approved"
            confidence_tier = "high"
        elif confidence >= settings.hitl.review_threshold:
            default_status = "needs_review"
            confidence_tier = "medium"
        else:
            default_status = "needs_review"
            confidence_tier = "low"

        content_cid = ext.get("content_component_id", f"p{page_num}-content")
        kv_list = ext.get("key_value_pairs", [])
        sig_list = ext.get("signatures", [])

        for item in kv_list:
            cid = item.get("component_id", "")
            if cid and cid in comp_decisions:
                item["decision"] = comp_decisions[cid]
        for item in sig_list:
            cid = item.get("component_id", "")
            if cid and cid in comp_decisions:
                item["decision"] = comp_decisions[cid]

        content_decision = comp_decisions.get(content_cid)

        status = _derive_page_status(
            default_status, page_num, decisions, comp_decisions, ext
        )

        if status in ("approved", "edited"):
            approved_count += 1
        elif status == "flagged":
            flagged_count += 1

        pages.append({
            "page_num": page_num,
            "confidence": confidence if isinstance(confidence, (int, float)) else 0.0,
            "confidence_tier": confidence_tier,
            "status": status,
            "markdown": _clean_md(markdown or ""),
            "page_width": ext.get("page_width"),
            "page_height": ext.get("page_height"),
            "page_unit": ext.get("page_unit"),
            "parser_repairs": ext.get("parser_repairs", []),
            "parser_repair_count": ext.get("parser_repair_count", 0),
            "parser_repair_severity": ext.get("parser_repair_severity", "none"),
            "parser_repair_severity_score": ext.get("parser_repair_severity_score", 0),
            "content_component_id": content_cid,
            "content_decision": content_decision,
            "handwritten_count": ext.get("handwritten_count", 0),
            "barcodes": ext.get("barcodes", []),
            "selection_marks": ext.get("selection_marks", []),
            "selection_semantics": ext.get("selection_semantics", {}),
            "packet_section_id": ext.get("packet_section_id", ""),
            "packet_section_name": ext.get("packet_section_name", ""),
            "packet_boundary_confidence": ext.get("packet_boundary_confidence", 0.0),
            "packet_boundary_reason": ext.get("packet_boundary_reason", ""),
            "extraction_family": ext.get("extraction_family", ""),
            "extraction_strategy_family": ext.get("extraction_strategy_family", ""),
            "template_family": ext.get("template_family", ""),
            "extraction_family_confidence": ext.get("extraction_family_confidence", 0.0),
            "extraction_family_reason": ext.get("extraction_family_reason", ""),
            "packet_anchor_issues": ext.get("packet_anchor_issues", []),
            "corruption_risk": ext.get("corruption_risk", {}),
            "query_field_merge_trace": ext.get("query_field_merge_trace", []),
            "review_priority_score": ext.get("review_priority_score", 0.0),
            "cross_field_issues": ext.get("cross_field_issues", []),
            "key_value_pairs": kv_list,
            "signatures": sig_list,
        })

    pages.sort(key=lambda p: p["page_num"])

    full_markdown = _clean_md(raw_markdown.get("full", ""))

    all_signatures = results.get("signatures", [])
    all_kv_pairs = results.get("key_value_pairs", [])
    all_languages = results.get("languages", [])

    return {
        "doc_id": doc_id,
        "total_pages": len(pages),
        "approved_count": approved_count,
        "flagged_count": flagged_count,
        "needs_review_count": len(pages) - approved_count - flagged_count,
        "full_markdown": full_markdown,
        "pages": pages,
        "packet_sections": results.get("packet_sections", []),
        "extraction_routing": results.get("extraction_routing", {}),
        "template_routing": results.get("template_routing", {}),
        "rollout_control": results.get("rollout_control", {}),
        "query_fields_results": results.get("query_fields_results", []),
        "shadow_custom_model": results.get("shadow_custom_model", {}),
        "field_confidence_summary": results.get("field_confidence_summary", {}),
        "cross_field_consistency": results.get("cross_field_consistency", {}),
        "review_priority_queue": results.get("review_priority_queue", []),
        "review_corrections": results.get("review_corrections", []),
        "correction_dictionary": results.get("correction_dictionary", {}),
        "ocr_confusion_map": results.get("ocr_confusion_map", {}),
        "correction_summary": results.get("correction_summary", {}),
        "retraining_trigger": results.get("retraining_trigger", {}),
        "packet_anchor_consensus": results.get("packet_anchor_consensus", {}),
        "packet_corruption_risk": results.get("packet_corruption_risk", {}),
        "document_quality": results.get("document_quality", {}),
        "signatures": all_signatures,
        "key_value_pairs": all_kv_pairs,
        "languages": all_languages,
    }


@router.post("/{doc_id}/pages/{page_num}/approve")
async def approve_page(doc_id: str, page_num: int):
    """Approve a page."""
    results = _load_results(doc_id)

    decisions = results.get("hitl_decisions", [])
    decisions = [d for d in decisions if not (isinstance(d, dict) and d.get("page_num") == page_num)]
    decisions.append({"page_num": page_num, "action": "approve", "status": "approved"})
    results["hitl_decisions"] = decisions

    _save_results(doc_id, results)
    return {"page_num": page_num, "action": "approved", "status": "success"}


@router.post("/{doc_id}/pages/{page_num}/edit")
async def edit_page(doc_id: str, page_num: int, body: EditPageBody):
    """Save edits to a page's extraction."""
    results = _load_results(doc_id)

    if body.markdown is not None:
        raw_markdown = results.get("raw_markdown", {})
        before = str(raw_markdown.get(str(page_num), raw_markdown.get(page_num, "")) or "")
        raw_markdown[str(page_num)] = body.markdown
        results["raw_markdown"] = raw_markdown
        _append_correction(results, {
            "source": "page_edit",
            "page_num": page_num,
            "field_id": "page_markdown",
            "before_value": before[:2000],
            "after_value": str(body.markdown)[:2000],
            "criticality": "major",
        })

    decisions = results.get("hitl_decisions", [])
    decisions = [d for d in decisions if not (isinstance(d, dict) and d.get("page_num") == page_num)]
    decisions.append({"page_num": page_num, "action": "edit", "status": "edited"})
    results["hitl_decisions"] = decisions

    _save_results(doc_id, results)
    return {"page_num": page_num, "action": "edited", "status": "success"}


@router.post("/{doc_id}/pages/{page_num}/flag")
async def flag_page(doc_id: str, page_num: int, body: FlagPageBody):
    """Flag a page for further review."""
    results = _load_results(doc_id)

    decisions = results.get("hitl_decisions", [])
    decisions = [d for d in decisions if not (isinstance(d, dict) and d.get("page_num") == page_num)]
    decision = {"page_num": page_num, "action": "flag", "status": "flagged"}
    if body.reason:
        decision["reason"] = body.reason
    decisions.append(decision)
    results["hitl_decisions"] = decisions

    _save_results(doc_id, results)
    return {"page_num": page_num, "action": "flagged", "status": "success"}


# ═══════════════════════════════════════════════════════════════
#  Export endpoints
# ═══════════════════════════════════════════════════════════════


def _build_final_markdown(doc_id: str) -> str:
    """Assemble the HITL-updated markdown, applying any component edits."""
    results = _load_results(doc_id)
    raw_markdown = results.get("raw_markdown", {})
    comp_decisions = results.get("component_decisions", {})
    extractions = results.get("extractions", [])
    total_pages = len(extractions)

    has_full = bool(raw_markdown.get("full"))

    if has_full:
        final = _clean_md(raw_markdown["full"])
    else:
        page_parts = []
        for page_num in range(1, total_pages + 1):
            page_md = _get_by_page(raw_markdown, page_num, "")
            content_cid = f"p{page_num}-content"
            content_dec = comp_decisions.get(content_cid, {})
            if content_dec.get("action") == "edit" and content_dec.get("value"):
                page_md = content_dec["value"]
            page_parts.append(_clean_md(page_md or ""))
        final = "\n\n---\n\n".join(page_parts)

    return final


_EXPORT_CSS = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, sans-serif;
         max-width: 900px; margin: 2rem auto; padding: 0 1.5rem; color: #1a1a2e; line-height: 1.7; font-size: 15px; }
  h1, h2, h3 { color: #16213e; margin-top: 1.5em; }
  h1 { font-size: 1.8em; border-bottom: 2px solid #e2e8f0; padding-bottom: 0.3em; }
  h2 { font-size: 1.4em; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.2em; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.9em; }
  th, td { border: 1px solid #d1d5db; padding: 0.5em 0.8em; text-align: left; vertical-align: top; }
  th { background: #f1f5f9; font-weight: 600; }
  tbody tr:nth-child(even) { background: #f8fafc; }
  code { background: #f1f5f9; padding: 0.15em 0.4em; border-radius: 3px; font-size: 0.9em; }
  pre { background: #f1f5f9; padding: 1em; border-radius: 6px; overflow-x: auto; }
  blockquote { border-left: 3px solid #6366f1; margin: 1em 0; padding: 0.5em 1em; background: #f8fafc; }
  hr { border: none; border-top: 1px solid #e2e8f0; margin: 2em 0; }
  @media print { body { max-width: 100%; margin: 0; } }
</style>
"""


@router.get("/{doc_id}/export")
async def export_document(
    doc_id: str,
    format: str = Query("html", pattern="^(md|html)$"),
):
    """Export the HITL-updated document as Markdown or styled HTML."""
    final_md = _build_final_markdown(doc_id)

    results = _load_results(doc_id)
    filename_raw = results.get("filename", doc_id)
    stem = Path(filename_raw).stem

    if format == "md":
        return Response(
            content=final_md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.md"'},
        )

    html_body = md_lib.markdown(
        final_md,
        extensions=["tables", "fenced_code", "toc", "nl2br"],
        output_format="html",
    )
    html_doc = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        f"<meta charset='utf-8'>\n<title>{stem}</title>\n"
        f"{_EXPORT_CSS}\n</head>\n<body>\n{html_body}\n</body>\n</html>"
    )

    return Response(
        content=html_doc,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stem}.html"'},
    )


# ═══════════════════════════════════════════════════════════════
#  Component-level HITL endpoints
# ═══════════════════════════════════════════════════════════════


@router.post("/{doc_id}/components/action")
async def component_action(doc_id: str, body: ComponentActionBody):
    """Approve, edit, or flag a single component by its component_id."""
    results = _load_results(doc_id)
    comp_decisions: dict = results.setdefault("component_decisions", {})

    decision: dict = {"action": body.action, "status": _action_to_status(body.action)}
    if body.value is not None:
        before = _lookup_component_value(results, body.component_id)
        decision["value"] = body.value
        _append_correction(results, {
            "source": "component_action",
            "page_num": _page_num_from_component_id(body.component_id),
            "component_id": body.component_id,
            "field_id": body.component_id,
            "before_value": before[:500],
            "after_value": str(body.value)[:500],
            "criticality": "major",
        })
    if body.reason:
        decision["reason"] = body.reason

    comp_decisions[body.component_id] = decision
    results["component_decisions"] = comp_decisions
    _save_results(doc_id, results)

    return {
        "component_id": body.component_id,
        "action": body.action,
        "status": "success",
    }


@router.post("/{doc_id}/components/bulk")
async def bulk_component_action(doc_id: str, body: BulkComponentActionBody):
    """Approve or flag multiple components at once (e.g. 'Approve All' on a page)."""
    results = _load_results(doc_id)
    comp_decisions: dict = results.setdefault("component_decisions", {})

    status = _action_to_status(body.action)
    for cid in body.component_ids:
        comp_decisions[cid] = {"action": body.action, "status": status}

    results["component_decisions"] = comp_decisions
    _save_results(doc_id, results)

    return {
        "component_ids": body.component_ids,
        "action": body.action,
        "status": "success",
    }


@router.get("/{doc_id}/components/{component_id}")
async def get_component_decision(doc_id: str, component_id: str):
    """Get the current decision for a specific component."""
    results = _load_results(doc_id)
    comp_decisions = results.get("component_decisions", {})
    decision = comp_decisions.get(component_id)
    return {
        "component_id": component_id,
        "decision": decision,
    }


def _action_to_status(action: str) -> str:
    return {"approve": "approved", "edit": "edited", "flag": "flagged"}.get(action, action)
