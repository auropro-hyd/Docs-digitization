"""Review-priority ranking derived from calibrated confidence and criticality."""

from __future__ import annotations

from typing import Any


_CRIT_WEIGHT = {"critical": 1.0, "major": 0.7, "minor": 0.4, "observation": 0.2}


def build_review_priority_queue(extractions: list[dict[str, Any]], cross_field: dict[str, Any]) -> list[dict[str, Any]]:
    discrepancy_pages = set()
    for d in cross_field.get("discrepancies", []):
        for p in d.get("pages", []) or []:
            discrepancy_pages.add(int(p))

    queue: list[dict[str, Any]] = []
    for ext in extractions or []:
        page_num = int(ext.get("page_num", 0) or 0)
        parser_score = float(ext.get("parser_repair_severity_score", 0) or 0)
        anchor_issues = len(ext.get("packet_anchor_issues", []) or [])

        kv_items = ext.get("key_value_pairs", []) or []
        if not kv_items:
            base_score = 30.0 + min(25.0, parser_score * 3.0)
            if page_num in discrepancy_pages:
                base_score += 20.0
            queue.append({
                "page_num": page_num,
                "component_id": ext.get("content_component_id", f"p{page_num}-content"),
                "priority_score": round(min(100.0, base_score), 2),
                "reason": "no_structured_fields",
                "field_id": "",
            })
            continue

        for kv in kv_items:
            conf = float(kv.get("calibrated_confidence", kv.get("confidence", 0.5)) or 0.5)
            crit = str(kv.get("criticality", "major"))
            crit_w = _CRIT_WEIGHT.get(crit, 0.6)
            score = (1.0 - conf) * 70.0 * crit_w
            score += min(20.0, parser_score * 2.0)
            score += min(10.0, anchor_issues * 4.0)
            if page_num in discrepancy_pages:
                score += 15.0
            queue.append({
                "page_num": page_num,
                "component_id": kv.get("component_id", ""),
                "field_id": kv.get("field_id", ""),
                "priority_score": round(min(100.0, score), 2),
                "reason": f"{crit}_field_low_confidence",
            })

    queue.sort(key=lambda x: x["priority_score"], reverse=True)
    return queue
