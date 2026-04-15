"""Reviewer correction aggregation and retraining trigger evaluation."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

_DATE_RE = re.compile(
    r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$"
    r"|^\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}$"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (O(min(m,n)) space)."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def infer_criticality(before: str, after: str, field_id: str = "") -> str:
    """Classify the severity of an OCR correction.

    Returns ``"minor"`` for small typos/OCR artifacts, ``"critical"`` for
    missing content or date corrections, and ``"major"`` as the default.
    """
    before_s = before.strip()
    after_s = after.strip()

    if not before_s and after_s:
        return "critical"
    if _DATE_RE.match(before_s) and _DATE_RE.match(after_s) and before_s != after_s:
        return "critical"
    if _edit_distance(before_s, after_s) <= 2:
        return "minor"
    return "major"


def build_correction_artifacts(corrections: list[dict[str, Any]]) -> dict[str, Any]:
    per_field_updates: Counter[str] = Counter()
    confusion_pairs: Counter[str] = Counter()
    critical_updates = 0

    for c in corrections or []:
        field_id = str(c.get("field_id", "")).strip() or "unknown_field"
        before = str(c.get("before_value", "") or "").strip()
        after = str(c.get("after_value", "") or "").strip()
        criticality = str(c.get("criticality", "major"))

        if before == after:
            continue
        per_field_updates[field_id] += 1
        if criticality == "critical":
            critical_updates += 1
        pair = f"{before} -> {after}"
        confusion_pairs[pair] += 1

    correction_dictionary = {
        "field_updates": dict(per_field_updates),
        "top_pairs": dict(confusion_pairs.most_common(50)),
    }
    total = max(1, sum(per_field_updates.values()))
    return {
        "correction_dictionary": correction_dictionary,
        "ocr_confusion_map": dict(confusion_pairs),
        "summary": {
            "total_corrections": sum(per_field_updates.values()),
            "critical_corrections": critical_updates,
            "critical_correction_rate": round(critical_updates / total, 4),
        },
    }


def evaluate_retraining_trigger(
    corrections: list[dict[str, Any]],
    *,
    threshold_correction_rate: float = 0.08,
    threshold_critical_rate: float = 0.03,
    min_corrections_for_trigger: int = 20,
) -> dict[str, Any]:
    artifacts = build_correction_artifacts(corrections)
    total = int(artifacts["summary"]["total_corrections"])
    critical_rate = float(artifacts["summary"]["critical_correction_rate"])

    # Without total reviewed fields/pages, use correction-volume proxy.
    correction_rate_proxy = min(1.0, total / 250.0)
    should_trigger = (
        total >= min_corrections_for_trigger
        and (
            correction_rate_proxy >= threshold_correction_rate
            or critical_rate >= threshold_critical_rate
        )
    )

    return {
        "should_trigger_retraining": should_trigger,
        "thresholds": {
            "correction_rate_proxy": threshold_correction_rate,
            "critical_correction_rate": threshold_critical_rate,
            "min_corrections": min_corrections_for_trigger,
        },
        "metrics": {
            "total_corrections": total,
            "correction_rate_proxy": round(correction_rate_proxy, 4),
            "critical_correction_rate": critical_rate,
        },
        "generated_at": utc_now_iso(),
    }
