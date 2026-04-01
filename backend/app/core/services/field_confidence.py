"""Calibrated field confidence scoring with decomposition factors."""

from __future__ import annotations

from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _field_criticality(field_id: str, critical_fields: list[str]) -> str:
    if field_id in set(critical_fields or []):
        return "critical"
    if any(tok in field_id for tok in ("batch", "bpcr", "mpcr", "product")):
        return "major"
    return "minor"


def calibrate_kv_confidence(
    kv: dict[str, Any],
    *,
    parser_repair_severity_score: float,
    selection_ambiguity: bool,
    anchor_issue_count: int,
    critical_fields: list[str],
) -> dict[str, Any]:
    field_id = str(kv.get("field_id", "")).strip()
    base = float(kv.get("confidence", 0.5) or 0.5)
    crit = _field_criticality(field_id, critical_fields)

    parser_penalty = _clamp(float(parser_repair_severity_score or 0) / 10.0) * 0.20
    placeholder_penalty = 0.0
    if bool(kv.get("is_placeholder", False)) and not bool(kv.get("placeholder_allowed", False)):
        placeholder_penalty = 0.35

    ambiguity_penalty = 0.15 if selection_ambiguity and "yes_no_na" in field_id else 0.0
    anchor_penalty = min(0.20, 0.07 * int(anchor_issue_count or 0))

    score = _clamp(base - parser_penalty - placeholder_penalty - ambiguity_penalty - anchor_penalty)
    out = dict(kv)
    out["criticality"] = crit
    out["calibrated_confidence"] = round(score, 4)
    out["confidence_decomposition"] = {
        "base_confidence": round(base, 4),
        "parser_penalty": round(parser_penalty, 4),
        "placeholder_penalty": round(placeholder_penalty, 4),
        "selection_ambiguity_penalty": round(ambiguity_penalty, 4),
        "anchor_consistency_penalty": round(anchor_penalty, 4),
    }
    return out


def summarize_field_confidence(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    values = []
    for ext in extractions or []:
        for kv in ext.get("key_value_pairs", []) or []:
            values.append(float(kv.get("calibrated_confidence", 0.0) or 0.0))
    if not values:
        return {"field_count": 0, "mean_calibrated_confidence": 0.0, "low_confidence_fields": 0}
    low = len([v for v in values if v < 0.65])
    return {
        "field_count": len(values),
        "mean_calibrated_confidence": round(sum(values) / len(values), 4),
        "low_confidence_fields": low,
    }
