"""Deterministic merge policy for query-fields and layout KV outputs."""

from __future__ import annotations

from typing import Any


def merge_query_fields(
    kv_records: list[dict[str, Any]],
    query_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged = [dict(kv) for kv in kv_records or []]
    index = {str(k.get("field_id", "")): i for i, k in enumerate(merged) if str(k.get("field_id", ""))}
    trace: list[dict[str, Any]] = []

    for q in query_records or []:
        field_id = str(q.get("field_id", "")).strip()
        if not field_id:
            continue
        q_val = str(q.get("normalized_value") or q.get("value") or "").strip()
        q_conf = float(q.get("confidence", 0.0) or 0.0)
        q_is_placeholder = bool(q.get("is_placeholder", False))

        if field_id not in index:
            rec = {
                "field_id": field_id,
                "key": q.get("key", field_id),
                "value": q.get("value", q_val),
                "raw_value": q.get("value", q_val),
                "normalized_value": q_val,
                "confidence": q_conf,
                "source": "query_fields",
            }
            merged.append(rec)
            index[field_id] = len(merged) - 1
            trace.append({"field_id": field_id, "action": "added_from_query", "reason": "missing_in_layout"})
            continue

        rec = merged[index[field_id]]
        kv_val = str(rec.get("normalized_value") or rec.get("value") or "").strip()
        kv_conf = float(rec.get("confidence", 0.0) or 0.0)
        kv_is_placeholder = bool(rec.get("is_placeholder", False))
        placeholder_allowed = bool(rec.get("placeholder_allowed", False))

        should_replace = False
        reason = ""
        if (not kv_val or (kv_is_placeholder and not placeholder_allowed)) and q_val:
            should_replace = True
            reason = "layout_missing_or_disallowed_placeholder"
        elif q_val and q_conf > kv_conf + 0.1 and (not q_is_placeholder or placeholder_allowed):
            should_replace = True
            reason = "higher_query_confidence"

        if should_replace:
            rec["value"] = q.get("value", q_val)
            rec["raw_value"] = q.get("value", q_val)
            rec["normalized_value"] = q_val
            rec["confidence"] = q_conf
            rec["source"] = "query_fields_override"
            trace.append({"field_id": field_id, "action": "replaced_with_query", "reason": reason})
        else:
            trace.append({"field_id": field_id, "action": "kept_layout", "reason": "layout_precedence"})

    return merged, trace
