"""Localized corruption-aware risk model for OCR pages."""

from __future__ import annotations

from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _risk_level(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _page_risk(ext: dict[str, Any], confidence: float) -> dict[str, Any]:
    parser_severity_score = float(ext.get("parser_repair_severity_score", 0) or 0)
    parser_component = _clamp(parser_severity_score / 10.0, 0, 1) * 40.0

    conf = _clamp(float(confidence or 0), 0, 1)
    confidence_component = _clamp((0.80 - conf) / 0.80, 0, 1) * 30.0

    selection = ext.get("selection_semantics", {}) or {}
    ambiguous = bool(selection.get("has_ambiguity", False))
    selection_component = 15.0 if ambiguous else 0.0

    anchor_issues = ext.get("packet_anchor_issues", []) or []
    anchor_component = min(15.0, len(anchor_issues) * 7.5)

    handwritten_count = int(ext.get("handwritten_count", 0) or 0)
    handwriting_component = 5.0 if handwritten_count >= 20 else 0.0

    total = round(parser_component + confidence_component + selection_component + anchor_component + handwriting_component, 2)
    return {
        "score": total,
        "level": _risk_level(total),
        "factors": {
            "parser_component": round(parser_component, 2),
            "confidence_component": round(confidence_component, 2),
            "selection_component": round(selection_component, 2),
            "anchor_component": round(anchor_component, 2),
            "handwriting_component": round(handwriting_component, 2),
        },
    }


def compute_packet_corruption_risk(extractions: list[dict], confidence_scores: dict[int, float]) -> dict[str, Any]:
    pages: dict[int, dict[str, Any]] = {}
    level_counts = {"low": 0, "medium": 0, "high": 0}
    max_score = 0.0

    for ext in extractions or []:
        page_num = int(ext.get("page_num", 0) or 0)
        confidence = float(confidence_scores.get(page_num, 0.5))
        risk = _page_risk(ext, confidence)
        pages[page_num] = risk
        level_counts[risk["level"]] += 1
        max_score = max(max_score, risk["score"])

    status = "stable"
    if level_counts["high"] > 0:
        status = "needs_attention"
    elif level_counts["medium"] > 0:
        status = "monitor"

    return {
        "status": status,
        "max_page_risk_score": round(max_score, 2),
        "level_counts": level_counts,
        "pages": pages,
    }
