"""Cross-field consistency checks for key compliance links."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def _to_num(value: str) -> float | None:
    text = str(value or "")
    cleaned = re.sub(r"[^0-9.\-]+", "", text)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def evaluate_cross_field_consistency(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    field_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ext in extractions or []:
        page_num = int(ext.get("page_num", 0) or 0)
        for kv in ext.get("key_value_pairs", []) or []:
            fid = str(kv.get("field_id", "")).strip()
            if not fid:
                continue
            field_hits[fid].append({
                "page_num": page_num,
                "value": str(kv.get("normalized_value") or kv.get("value") or "").strip(),
            })

    discrepancies: list[dict[str, Any]] = []

    for anchor in ("batch_no", "product_name", "bpcr_number", "mpcr_number", "ar_no"):
        values = [h["value"] for h in field_hits.get(anchor, []) if h["value"]]
        uniq = sorted(set(values))
        if len(uniq) > 1:
            discrepancies.append({
                "type": "anchor_mismatch",
                "field_id": anchor,
                "expected": uniq[0],
                "observed_values": uniq[1:],
                "pages": sorted({h["page_num"] for h in field_hits.get(anchor, [])}),
            })

    weighed = [h["value"] for h in field_hits.get("raw_material_weighed_total", []) if h["value"]]
    used = [h["value"] for h in field_hits.get("material_usage_total", []) if h["value"]]
    if weighed and used:
        n1 = _to_num(weighed[-1])
        n2 = _to_num(used[-1])
        if n1 is not None and n2 is not None:
            ref = max(abs(n1), 1.0)
            rel_gap = abs(n1 - n2) / ref
            if rel_gap > 0.01:
                discrepancies.append({
                    "type": "material_reconciliation_gap",
                    "field_id": "material_usage_total",
                    "expected": weighed[-1],
                    "observed_values": [used[-1]],
                    "gap_ratio": round(rel_gap, 4),
                    "pages": sorted(
                        {h["page_num"] for h in field_hits.get("raw_material_weighed_total", [])}
                        | {h["page_num"] for h in field_hits.get("material_usage_total", [])}
                    ),
                })

    sample_flags = [h["value"].lower() for h in field_hits.get("sample_sent_to_qcd", []) if h["value"]]
    if any(v in {"yes", "y", "true", "1", "checked", "selected", "☑"} for v in sample_flags):
        has_report = any(field_hits.get(fid) for fid in ("qc_report_no", "ar_no", "analysis_report_id", "certificate_of_analysis"))
        if not has_report:
            discrepancies.append({
                "type": "missing_linked_qc_report",
                "field_id": "sample_sent_to_qcd",
                "expected": "linked_qc_report_present",
                "observed_values": ["not_found"],
                "pages": sorted({h["page_num"] for h in field_hits.get("sample_sent_to_qcd", [])}),
            })

    return {
        "status": "warning" if discrepancies else "ok",
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
    }
