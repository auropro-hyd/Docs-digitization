"""Shadow-run comparison helper for custom model path."""

from __future__ import annotations

from typing import Any


def summarize_shadow_delta(
    baseline_kv: list[dict[str, Any]],
    custom_kv: list[dict[str, Any]],
) -> dict[str, Any]:
    base_map = {str(k.get("field_id", "")): k for k in baseline_kv if str(k.get("field_id", ""))}
    cust_map = {str(k.get("field_id", "")): k for k in custom_kv if str(k.get("field_id", ""))}
    all_fields = sorted(set(base_map) | set(cust_map))

    changed = 0
    gained = 0
    for f in all_fields:
        b = str((base_map.get(f) or {}).get("normalized_value") or "")
        c = str((cust_map.get(f) or {}).get("normalized_value") or "")
        if b != c:
            changed += 1
        if not b and c:
            gained += 1

    return {
        "baseline_field_count": len(base_map),
        "custom_field_count": len(cust_map),
        "changed_fields": changed,
        "new_fields_from_custom": gained,
    }
