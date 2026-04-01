"""Config-driven extraction family assignment for packet sections."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

_RULES_DIR = Path(__file__).resolve().parents[2] / "compliance" / "rules"
_EXTRACTION_PROFILES_FILE = _RULES_DIR / "extraction_profiles.yaml"


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("-", " ").replace("_", " ").split())


class FamilyPolicy(BaseModel):
    display_name: str = ""
    name_keywords: list[str] = Field(default_factory=list)
    critical_fields: list[str] = Field(default_factory=list)
    selection_semantics_mode: str = "standard"

    @model_validator(mode="after")
    def _normalize(self):
        self.name_keywords = [_norm(k) for k in self.name_keywords if _norm(k)]
        return self


class FamilyDefaults(BaseModel):
    fallback_family: str = "general_records"
    fallback_order: list[str] = Field(default_factory=list)
    anchor_identifiers: dict[str, list[str]] = Field(default_factory=dict)
    minimum_consensus_ratio: float = 0.75


class ExtractionProfilesConfig(BaseModel):
    version: int = 1
    families: dict[str, FamilyPolicy] = Field(default_factory=dict)
    defaults: FamilyDefaults = Field(default_factory=FamilyDefaults)

    @model_validator(mode="after")
    def _normalize(self):
        self.families = {_norm(k).replace(" ", "_"): v for k, v in self.families.items()}
        self.defaults.fallback_family = _norm(self.defaults.fallback_family).replace(" ", "_")
        self.defaults.fallback_order = [_norm(v).replace(" ", "_") for v in self.defaults.fallback_order if _norm(v)]
        self.defaults.anchor_identifiers = {
            _norm(anchor).replace(" ", "_"): [_norm(s) for s in synonyms if _norm(s)]
            for anchor, synonyms in self.defaults.anchor_identifiers.items()
        }
        self.defaults.minimum_consensus_ratio = max(0.0, min(1.0, self.defaults.minimum_consensus_ratio))
        return self


@lru_cache
def load_extraction_profiles() -> ExtractionProfilesConfig:
    raw = yaml.safe_load(_EXTRACTION_PROFILES_FILE.read_text(encoding="utf-8")) or {}
    return ExtractionProfilesConfig.model_validate(raw)


def resolve_family(section_name: str) -> tuple[str, float, str]:
    """Resolve family id from a section name deterministically."""
    config = load_extraction_profiles()
    text = _norm(section_name)
    if not text:
        fam = config.defaults.fallback_family
        return fam, 0.0, "empty_section_name"

    best_family = config.defaults.fallback_family
    best_score = 0.0
    best_reason = "fallback_family"
    words = set(text.split())

    for family_id, policy in config.families.items():
        score = 0.0
        matched = 0
        for kw in policy.name_keywords:
            if kw and kw in text:
                score += 1.0
                matched += 1
            else:
                kw_words = set(kw.split())
                if kw_words and len(words & kw_words) >= max(1, len(kw_words) // 2):
                    score += 0.45
                    matched += 1

        if score > best_score:
            best_score = score
            best_family = family_id
            best_reason = f"matched_keywords:{matched}"

    confidence = min(1.0, best_score / 2.5) if best_score > 0 else 0.2
    return best_family, round(confidence, 3), best_reason


def enrich_packet_sections_with_family(packet_sections: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for sec in packet_sections or []:
        family_id, conf, reason = resolve_family(sec.get("name", ""))
        item = dict(sec)
        item["extraction_family"] = family_id
        item["extraction_family_confidence"] = conf
        item["extraction_family_reason"] = reason
        enriched.append(item)
    return enriched


def get_family_policy(family_id: str) -> FamilyPolicy | None:
    config = load_extraction_profiles()
    return config.families.get(_norm(family_id).replace(" ", "_"))
