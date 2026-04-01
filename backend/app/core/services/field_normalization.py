"""Deterministic field normalization and placeholder semantics."""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_RULES_DIR = Path(__file__).resolve().parents[2] / "compliance" / "rules"
_FIELD_POLICIES_FILE = _RULES_DIR / "field_policies.yaml"

_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _NON_ALNUM_RE.sub("_", str(value or "").strip().lower()).strip("_")


def _normalize_ws(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())


@lru_cache
def _load_placeholder_policy() -> dict[str, Any]:
    raw = yaml.safe_load(_FIELD_POLICIES_FILE.read_text(encoding="utf-8")) or {}
    sem = raw.get("placeholder_semantics", {}) if isinstance(raw, dict) else {}
    return {
        "global_allowed_values": {_normalize_ws(v).lower() for v in sem.get("global_allowed_values", [])},
        "defaults": {_slug(v) for v in sem.get("defaults", {}).get("allowed_field_ids", [])},
        "by_family": {
            _slug(fam): {_slug(v) for v in cfg.get("allowed_field_ids", [])}
            for fam, cfg in (sem.get("by_family", {}) or {}).items()
        },
    }


def _normalize_date(value: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


def _normalize_identifier(value: str) -> str:
    text = _normalize_ws(value).upper()
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s*/\s*", "/", text)
    return text


def normalize_field_value(field_id: str, raw_value: str) -> tuple[str, list[str]]:
    fid = _slug(field_id)
    value = _normalize_ws(raw_value)
    reasons: list[str] = []

    if value != str(raw_value or ""):
        reasons.append("trimmed_whitespace")

    if any(tok in fid for tok in ("date", "effective_date")):
        parsed = _normalize_date(value)
        if parsed and parsed != value:
            value = parsed
            reasons.append("normalized_date_format")

    if any(tok in fid for tok in ("batch", "bpcr", "mpcr", "ar_no", "id", "number", "no")):
        normalized = _normalize_identifier(value)
        if normalized != value:
            value = normalized
            reasons.append("normalized_identifier")

    if any(tok in fid for tok in ("size", "qty", "quantity", "weight")):
        lowered = re.sub(r"\bKG\b", "kg", value, flags=re.IGNORECASE)
        lowered = re.sub(r"\bGM\b", "g", lowered, flags=re.IGNORECASE)
        lowered = _normalize_ws(lowered)
        if lowered != value:
            value = lowered
            reasons.append("normalized_units")

    return value, reasons


def evaluate_placeholder(field_id: str, value: str, *, family: str = "") -> tuple[bool, bool, str]:
    policy = _load_placeholder_policy()
    normalized_value = _normalize_ws(value).lower()
    is_placeholder = normalized_value in policy["global_allowed_values"]
    if not is_placeholder:
        return False, False, "not_placeholder"

    fid = _slug(field_id)
    fam = _slug(family)
    defaults = policy["defaults"]
    family_allowed = policy["by_family"].get(fam, set())
    allowed = fid in defaults or fid in family_allowed
    reason = "allowed_by_field_policy" if allowed else "not_allowed_for_field"
    return True, allowed, reason


def normalize_kv_record(kv: dict[str, Any], *, family: str = "") -> dict[str, Any]:
    key_text = str(kv.get("key") or kv.get("key_text") or "").strip()
    field_id = _slug(key_text)
    raw_value = str(kv.get("value") or kv.get("value_text") or "")
    normalized, reasons = normalize_field_value(field_id, raw_value)
    is_placeholder, placeholder_allowed, placeholder_reason = evaluate_placeholder(field_id, normalized, family=family)
    out = dict(kv)
    out["field_id"] = field_id
    out["raw_value"] = raw_value
    out["normalized_value"] = normalized
    out["normalization_reason_codes"] = reasons
    out["is_placeholder"] = is_placeholder
    out["placeholder_allowed"] = placeholder_allowed
    out["placeholder_reason"] = placeholder_reason
    return out
