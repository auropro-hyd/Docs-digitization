"""Declarative reporting configuration (Spec 004 §2.3 / §4).

Two YAML files live under ``config/rules/<pilot>/reporting/``:

* ``report-severity-gating.yaml`` — which severities block export, with
  optional rule-level overrides.
* ``report-sections.yaml``         — render ordering for groups and
  sub-sections, and the catch-all bucket title.

Both manifests are optional; callers fall back to v0 defaults when a
bundle does not ship them (useful for unit tests that don't need the
full pilot bundle).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.bmr.hitl.models import GroupKind, SubSectionKind

_DEFAULT_BLOCKING: frozenset[str] = frozenset({"critical", "major"})


class ReportingConfigError(ValueError):
    """Raised when a reporting YAML is malformed."""


# ── severity gating ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeverityRuleOverride:
    rule_id: str
    blocking: bool


@dataclass(frozen=True)
class SeverityGatingConfig:
    """Config for ``export_gate`` evaluation."""

    blocking_severities: frozenset[str] = _DEFAULT_BLOCKING
    rule_overrides: tuple[SeverityRuleOverride, ...] = ()

    def is_blocking(self, *, rule_id: str | None, severity: str) -> bool:
        for override in self.rule_overrides:
            if override.rule_id == rule_id:
                return override.blocking
        return severity.lower() in self.blocking_severities


# ── sections ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SectionSpec:
    kind: GroupKind
    title_template: str


@dataclass(frozen=True)
class ReportSectionsConfig:
    sub_section_order: tuple[SubSectionKind, ...] = (
        SubSectionKind.ALCOA,
        SubSectionKind.GMP,
        SubSectionKind.CHECKLIST,
    )
    group_order: tuple[SectionSpec, ...] = (
        SectionSpec(GroupKind.BPCR_STEP, "BPCR Step {step_number}"),
        SectionSpec(GroupKind.DOCUMENT_SCOPE, "Document scope: {document_ref_id}"),
    )
    catch_all_title: str = "Other findings"

    def render_title(self, *, group_kind: GroupKind, group_ref: dict[str, Any]) -> str:
        for spec in self.group_order:
            if spec.kind is group_kind:
                try:
                    return spec.title_template.format(**group_ref)
                except KeyError:
                    return self.catch_all_title
        return self.catch_all_title

    def group_rank(self, group_kind: GroupKind) -> int:
        for idx, spec in enumerate(self.group_order):
            if spec.kind is group_kind:
                return idx
        return len(self.group_order) + 1


# ── combined bundle ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReportingConfig:
    severity: SeverityGatingConfig = field(default_factory=SeverityGatingConfig)
    sections: ReportSectionsConfig = field(default_factory=ReportSectionsConfig)

    @classmethod
    def default(cls) -> ReportingConfig:
        return cls()


# ── loaders ──────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReportingConfigError(f"missing reporting manifest: {path}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ReportingConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportingConfigError(f"{path} must be a YAML mapping")
    return data


def load_severity_gating(path: Path) -> SeverityGatingConfig:
    data = _load_yaml(path)
    blocking_raw = data.get("blocking_severities") or []
    if not isinstance(blocking_raw, list):
        raise ReportingConfigError("blocking_severities must be a list")
    overrides_raw = data.get("rule_overrides") or []
    if not isinstance(overrides_raw, list):
        raise ReportingConfigError("rule_overrides must be a list")
    overrides: list[SeverityRuleOverride] = []
    for item in overrides_raw:
        if not isinstance(item, dict) or "rule_id" not in item:
            raise ReportingConfigError(
                "rule_overrides entries must be mappings with a rule_id"
            )
        overrides.append(
            SeverityRuleOverride(
                rule_id=str(item["rule_id"]),
                blocking=bool(item.get("blocking", True)),
            )
        )
    return SeverityGatingConfig(
        blocking_severities=frozenset(str(s).lower() for s in blocking_raw),
        rule_overrides=tuple(overrides),
    )


def load_report_sections(path: Path) -> ReportSectionsConfig:
    data = _load_yaml(path)
    sub_raw = data.get("sub_section_order") or []
    if not isinstance(sub_raw, list):
        raise ReportingConfigError("sub_section_order must be a list")
    sub_order = tuple(_coerce_sub_kind(value) for value in sub_raw) or (
        SubSectionKind.ALCOA,
        SubSectionKind.GMP,
        SubSectionKind.CHECKLIST,
    )
    groups_raw = data.get("group_order") or []
    if not isinstance(groups_raw, list):
        raise ReportingConfigError("group_order must be a list")
    group_specs: list[SectionSpec] = []
    for item in groups_raw:
        if not isinstance(item, dict) or "kind" not in item:
            raise ReportingConfigError("group_order entries must have a kind")
        group_specs.append(
            SectionSpec(
                kind=_coerce_group_kind(item["kind"]),
                title_template=str(
                    item.get("title_template", item["kind"])
                ),
            )
        )
    catch_all = (data.get("catch_all") or {}).get("title", "Other findings")
    return ReportSectionsConfig(
        sub_section_order=sub_order,
        group_order=tuple(group_specs) or ReportSectionsConfig().group_order,
        catch_all_title=str(catch_all),
    )


def load_reporting_config(bundle_dir: Path) -> ReportingConfig:
    """Load the bundle ``reporting/`` directory, falling back to defaults."""

    reporting_dir = bundle_dir / "reporting"
    severity_path = reporting_dir / "report-severity-gating.yaml"
    sections_path = reporting_dir / "report-sections.yaml"
    severity = (
        load_severity_gating(severity_path)
        if severity_path.is_file()
        else SeverityGatingConfig()
    )
    sections = (
        load_report_sections(sections_path)
        if sections_path.is_file()
        else ReportSectionsConfig()
    )
    return ReportingConfig(severity=severity, sections=sections)


def _coerce_sub_kind(value: Any) -> SubSectionKind:
    try:
        return SubSectionKind(str(value).lower())
    except ValueError as exc:
        raise ReportingConfigError(f"unknown sub-section kind: {value!r}") from exc


def _coerce_group_kind(value: Any) -> GroupKind:
    try:
        return GroupKind(str(value).lower())
    except ValueError as exc:
        raise ReportingConfigError(f"unknown group kind: {value!r}") from exc


def merge_blocking_severities(
    override: Iterable[str] | None, config: SeverityGatingConfig
) -> frozenset[str]:
    """Utility for callers that want to pass an ad-hoc override alongside config."""

    if override is None:
        return config.blocking_severities
    return frozenset(str(s).lower() for s in override)


__all__ = [
    "ReportSectionsConfig",
    "ReportingConfig",
    "ReportingConfigError",
    "SectionSpec",
    "SeverityGatingConfig",
    "SeverityRuleOverride",
    "load_report_sections",
    "load_reporting_config",
    "load_severity_gating",
    "merge_blocking_severities",
]
