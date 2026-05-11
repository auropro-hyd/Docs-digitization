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
    # Header phrases identifying a table column as a signature /
    # initials / attribution column. Consumed by the OCR signature
    # enricher; treated as case-insensitive substrings after
    # whitespace normalization. Empty list = enrichment disabled
    # for that profile.
    signature_column_headers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self):
        self.document_profiles = {
            _slug(k): v for k, v in self.document_profiles.items()
        }
        self.section_aliases = {
            _slug(k): _slug(v) for k, v in self.section_aliases.items()
        }
        # Normalize signature column headers to lowercase trimmed
        # form so the matcher in signature_enricher can do simple
        # substring tests without re-normalizing on every call.
        self.signature_column_headers = [
            " ".join(h.lower().split())
            for h in self.signature_column_headers
            if str(h).strip()
        ]
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


def infer_document_type_for_section_type(section_type: str) -> str | None:
    """Return the canonical ``document_type`` for a given ``section_type``.

    Two inference paths are attempted, in order:

    1. **Sub-section ownership** — the section_type appears in exactly
       one document profile's ``expected_sections`` list. This covers
       the common case where the LLM emits a fine-grained section_type
       like ``manufacturing_operations`` or ``material_request``.
    2. **Whole-document-as-section** — the section_type equals a
       document_type slug or one of its aliases. This covers the
       "the entire IPC report is a single section" case, where the
       segmentation LLM emits ``section_type="in_process_report"``
       for a document whose profile has empty ``expected_sections``.

    Returns ``None`` when the section_type is ambiguous (listed under
    multiple profiles) or unknown — callers are expected to leave the
    field empty so the downstream cross-document filter degrades to
    section-type-only matching rather than guessing the wrong owner.
    """

    canonical = normalize_section_type(section_type)
    if not canonical:
        return None

    profiles = load_profiles()

    # Path 1: sub-section ownership.
    matches: list[str] = []
    for doc_type, profile in profiles.document_profiles.items():
        for sec in profile.expected_sections:
            if canonical == sec.section_type or canonical in sec.aliases:
                matches.append(doc_type)
                break
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    # Path 2: whole-document-as-section. The section_type itself names
    # a document_type — the whole document is being treated as a
    # single section (typical for ipc_report, scada_report).
    doc_via_norm = normalize_document_type(canonical)
    if doc_via_norm in profiles.document_profiles:
        return doc_via_norm

    return None


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

    # Lazy import keeps profiles.py free of a runtime dep on cross_page;
    # the registered requirement IDs are the only string-shape values
    # that resolve at runtime, so they're the only ones validated here.
    from app.compliance.cross_page.interface import _REQUIREMENTS

    known_requirement_ids = set(_REQUIREMENTS.keys())

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

            for csr in rule.cross_section_requirements:
                # Two valid shapes: a registered requirement-ID string,
                # or an inline ``{section_type, in_document_type}``
                # dict. Anything else means the rule loader saw a
                # malformed YAML entry.
                if isinstance(csr, str):
                    if csr not in known_requirement_ids:
                        errors.append(
                            f"{rule.id}: cross_section_requirements references "
                            f"unknown requirement_id '{csr}'"
                        )
                elif isinstance(csr, dict):
                    sec = _slug(str(csr.get("section_type") or ""))
                    doc = _slug(str(csr.get("in_document_type") or ""))
                    if not sec and not doc:
                        errors.append(
                            f"{rule.id}: inline cross_section_requirement is "
                            f"empty (needs section_type and/or in_document_type)"
                        )
                    if sec and sec not in known_sections:
                        errors.append(
                            f"{rule.id}: inline cross_section_requirement "
                            f"section_type '{csr.get('section_type')}' is unknown"
                        )
                    if doc and doc not in known_docs:
                        errors.append(
                            f"{rule.id}: inline cross_section_requirement "
                            f"in_document_type '{csr.get('in_document_type')}' is unknown"
                        )
                else:
                    errors.append(
                        f"{rule.id}: cross_section_requirements entry has "
                        f"unsupported shape {type(csr).__name__}"
                    )

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
