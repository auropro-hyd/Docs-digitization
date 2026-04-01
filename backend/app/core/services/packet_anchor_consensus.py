"""Packet-level anchor consensus checks for key identifiers."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.core.services.extraction_family_policy import load_extraction_profiles


def _norm_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _norm_anchor_value(value: str) -> str:
    text = _norm_text(value)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _extract_kv_text(kv: dict[str, Any]) -> tuple[str, str]:
    key = str(kv.get("key") or kv.get("key_text") or "").strip()
    value = str(kv.get("value") or kv.get("value_text") or "").strip()
    return key, value


def evaluate_packet_anchor_consensus(extractions: list[dict]) -> dict[str, Any]:
    """
    Evaluate cross-section consistency for anchor identifiers (e.g. batch number).

    Returns summary with page-level issues that can be attached to extractions.
    """
    config = load_extraction_profiles()
    anchors = config.defaults.anchor_identifiers or {}
    min_ratio = config.defaults.minimum_consensus_ratio

    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for ext in extractions or []:
        page_num = int(ext.get("page_num", 0) or 0)
        for kv in ext.get("key_value_pairs", []) or []:
            key, value = _extract_kv_text(kv)
            if not key or not value:
                continue
            key_norm = _norm_text(key)
            value_norm = _norm_anchor_value(value)
            if not value_norm:
                continue
            for anchor_id, synonyms in anchors.items():
                if any(s and s in key_norm for s in synonyms):
                    observed[anchor_id].append({
                        "page_num": page_num,
                        "key": key,
                        "value": value,
                        "normalized_value": value_norm,
                    })

    summary: dict[str, Any] = {
        "anchors": {},
        "inconsistencies": [],
        "status": "ok",
    }

    for anchor_id, hits in observed.items():
        total = len(hits)
        counts: dict[str, int] = defaultdict(int)
        for item in hits:
            counts[item["normalized_value"]] += 1
        if not counts:
            continue
        winner_value = max(counts, key=counts.get)
        winner_count = counts[winner_value]
        ratio = winner_count / total if total else 1.0
        consistent = ratio >= min_ratio
        raw_winner = next((h["value"] for h in hits if h["normalized_value"] == winner_value), winner_value)

        summary["anchors"][anchor_id] = {
            "consensus_value": raw_winner,
            "consensus_ratio": round(ratio, 3),
            "total_observations": total,
            "unique_values": len(counts),
            "consistent": consistent,
        }

        if not consistent:
            summary["status"] = "warning"
            conflicts = [h for h in hits if h["normalized_value"] != winner_value]
            summary["inconsistencies"].append({
                "anchor_id": anchor_id,
                "expected": raw_winner,
                "conflicts": conflicts,
            })

    return summary


def page_anchor_issues(page_num: int, anchor_summary: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in anchor_summary.get("inconsistencies", []):
        anchor_id = item.get("anchor_id", "")
        expected = item.get("expected", "")
        for conflict in item.get("conflicts", []):
            if int(conflict.get("page_num", -1)) != int(page_num):
                continue
            issues.append({
                "anchor_id": anchor_id,
                "expected": expected,
                "observed": conflict.get("value", ""),
                "key": conflict.get("key", ""),
            })
    return issues
