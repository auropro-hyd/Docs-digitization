"""Deterministic extraction strategy router by packet family distribution."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.services.extraction_family_policy import get_family_policy, load_extraction_profiles


def route_extraction_strategy(packet_sections: list[dict]) -> dict[str, Any]:
    config = load_extraction_profiles()
    scores: Counter[str] = Counter()
    trace: list[dict[str, Any]] = []

    for sec in packet_sections or []:
        family = str(sec.get("extraction_family", "") or config.defaults.fallback_family)
        start_page = int(sec.get("start_page", 0) or 0)
        end_page = int(sec.get("end_page", start_page) or start_page)
        pages = max(1, end_page - start_page + 1)
        confidence = float(sec.get("extraction_family_confidence", 0.2) or 0.2)
        contribution = round(pages * max(0.1, confidence), 3)
        scores[family] += contribution
        trace.append({
            "section_id": sec.get("section_id", ""),
            "family": family,
            "pages": pages,
            "confidence": confidence,
            "contribution": contribution,
        })

    primary_family = scores.most_common(1)[0][0] if scores else config.defaults.fallback_family
    policy = get_family_policy(primary_family)

    fallback_order = config.defaults.fallback_order or [config.defaults.fallback_family]
    if primary_family in fallback_order:
        fallback_order = [primary_family] + [f for f in fallback_order if f != primary_family]
    else:
        fallback_order = [primary_family] + fallback_order

    return {
        "primary_family": primary_family,
        "family_scores": dict(scores),
        "fallback_order": fallback_order,
        "critical_fields": list(policy.critical_fields if policy else []),
        "selection_semantics_mode": str(policy.selection_semantics_mode if policy else "standard"),
        "trace": trace,
    }
