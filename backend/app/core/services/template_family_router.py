"""Template-family routing for custom/composed extraction models."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_RULES_DIR = Path(__file__).resolve().parents[2] / "compliance" / "rules"
_FILE = _RULES_DIR / "custom_model_profiles.yaml"


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().replace("_", " ").split())


@lru_cache
def _load() -> dict[str, Any]:
    raw = yaml.safe_load(_FILE.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def route_template_family(packet_sections: list[dict[str, Any]], extraction_family: str = "") -> dict[str, Any]:
    cfg = _load()
    families = cfg.get("template_families", {}) or {}
    fallback = cfg.get("fallback", {}) or {}

    probe = _norm(extraction_family)
    for sec in packet_sections or []:
        name = _norm(sec.get("name", ""))
        if name:
            probe += " " + name

    matched = ""
    for fam, entry in families.items():
        aliases = [_norm(a) for a in entry.get("aliases", [])]
        if any(a and a in probe for a in aliases):
            matched = fam
            break

    if not matched:
        matched = str(fallback.get("default_family", "bpr_core"))

    fam_cfg = families.get(matched, {})
    return {
        "template_family": matched,
        "enable_custom_model": bool(fam_cfg.get("enable_custom_model", False)),
        "use_baseline_when_unknown": bool(fallback.get("use_baseline_when_unknown", True)),
    }
