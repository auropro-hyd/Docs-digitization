"""Document profile loading, normalization, and config validation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from app.compliance.rules.registry import RuleRegistry

_RULES_DIR = Path(__file__).resolve().parent
_PROFILES_FILE = _RULES_DIR / "document_profiles.yaml"


def _slug(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


class ProfileSection(BaseModel):
    section_type: str
    display_name: str = ""
    required: bool = False
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self):
        self.section_type = _slug(self.section_type)
        self.aliases = [_slug(a) for a in self.aliases if str(a).strip()]
        return self


class DocumentProfile(BaseModel):
    aliases: list[str] = Field(default_factory=list)
    expected_sections: list[ProfileSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self):
        self.aliases = [_slug(a) for a in self.aliases if str(a).strip()]
        return self


class ProfilesConfig(BaseModel):
    version: int = 1
    document_profiles: dict[str, DocumentProfile] = Field(default_factory=dict)
    section_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize(self):
        self.document_profiles = {
            _slug(k): v for k, v in self.document_profiles.items()
        }
        self.section_aliases = {
            _slug(k): _slug(v) for k, v in self.section_aliases.items()
        }
        return self

    def known_document_types(self) -> set[str]:
        return set(self.document_profiles.keys())

    def known_section_types(self) -> set[str]:
        known: set[str] = set()
        for prof in self.document_profiles.values():
            for sec in prof.expected_sections:
                known.add(sec.section_type)
                known.update(sec.aliases)
        known.update(self.section_aliases.keys())
        known.update(self.section_aliases.values())
        return known


@lru_cache
def load_profiles() -> ProfilesConfig:
    raw = yaml.safe_load(_PROFILES_FILE.read_text(encoding="utf-8")) or {}
    return ProfilesConfig.model_validate(raw)


def normalize_document_type(document_type: str) -> str:
    value = _slug(document_type)
    profiles = load_profiles()
    if value in profiles.document_profiles:
        return value
    for canonical, profile in profiles.document_profiles.items():
        if value in profile.aliases:
            return canonical
    return value


def normalize_section_type(section_type: str) -> str:
    value = _slug(section_type)
    profiles = load_profiles()

    if value in profiles.section_aliases:
        value = profiles.section_aliases[value]

    for profile in profiles.document_profiles.values():
        for sec in profile.expected_sections:
            if value == sec.section_type or value in sec.aliases:
                return sec.section_type
    return value


def validate_compliance_configs(
    registry: RuleRegistry, *, strict: bool | None = None
) -> None:
    """Validate that rule applicability references resolve to known
    document / section types.

    Behaviour:

    * ``strict=True`` (or ``AT_COMPLIANCE__VALIDATE_STRICT=1``): raise
      ``ValueError`` on the first batch of errors. Use in CI to catch
      profile/rule drift early.
    * ``strict=False`` (default in long-running processes): emit a
      single ``WARNING`` log line summarising the drift and continue.
      Rules pointing at unknown types simply won't apply at runtime —
      the applicability gate already filters them out — so the app
      stays bootable while config authors converge.

    The default tracks ``AT_COMPLIANCE__VALIDATE_STRICT`` (truthy =
    strict). Production deployments that want fail-fast can set it; dev
    iteration where rules and profiles are landing on different
    schedules stays unblocked.
    """

    profiles = load_profiles()
    known_docs = profiles.known_document_types()
    known_sections = profiles.known_section_types()

    errors: list[str] = []

    for agent in registry.agents:
        for rule in registry.get_rules(agent):
            for doc in rule.applicable_document_types:
                if _slug(doc) not in known_docs:
                    errors.append(f"{rule.id}: unknown applicable_document_type '{doc}'")
            for doc in rule.excluded_document_types:
                if _slug(doc) not in known_docs:
                    errors.append(f"{rule.id}: unknown excluded_document_type '{doc}'")
            for sec in rule.applicable_section_types:
                if _slug(sec) not in known_sections:
                    errors.append(f"{rule.id}: unknown applicable_section_type '{sec}'")

    if not errors:
        return

    if strict is None:
        import os

        strict = os.getenv("AT_COMPLIANCE__VALIDATE_STRICT", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    msg = "Compliance config validation found drift:\n- " + "\n- ".join(errors[:50])
    if len(errors) > 50:
        msg += f"\n- ... plus {len(errors) - 50} more errors"

    if strict:
        raise ValueError(msg)

    import logging

    logging.getLogger(__name__).warning(
        "compliance.config.validation_drift count=%d (rules will skip mismatched "
        "applicability at runtime; set AT_COMPLIANCE__VALIDATE_STRICT=1 to fail "
        "the boot). First findings:\n%s",
        len(errors),
        msg,
    )
