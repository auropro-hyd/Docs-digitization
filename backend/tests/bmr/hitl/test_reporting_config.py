"""Tests for ``app.bmr.hitl.reporting_config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.hitl.models import GroupKind, SubSectionKind
from app.bmr.hitl.reporting_config import (
    ReportingConfigError,
    SeverityGatingConfig,
    SeverityRuleOverride,
    load_report_sections,
    load_reporting_config,
    load_severity_gating,
)

PILOT_REPORTING = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "rules"
    / "pilot"
    / "reporting"
)


def test_pilot_severity_gating_loads() -> None:
    cfg = load_severity_gating(PILOT_REPORTING / "report-severity-gating.yaml")
    assert "critical" in cfg.blocking_severities
    assert "major" in cfg.blocking_severities
    assert cfg.rule_overrides == ()


def test_pilot_sections_loads() -> None:
    cfg = load_report_sections(PILOT_REPORTING / "report-sections.yaml")
    assert cfg.sub_section_order[0] is SubSectionKind.ALCOA
    assert cfg.group_order[0].kind is GroupKind.BPCR_STEP
    title = cfg.render_title(
        group_kind=GroupKind.BPCR_STEP, group_ref={"step_number": 3}
    )
    assert title == "BPCR Step 3"


def test_reporting_config_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_reporting_config(tmp_path)
    assert cfg.severity.blocking_severities == frozenset({"critical", "major"})
    assert cfg.sections.group_order[0].kind is GroupKind.BPCR_STEP


def test_rule_override_wins_over_default() -> None:
    cfg = SeverityGatingConfig(
        blocking_severities=frozenset({"critical"}),
        rule_overrides=(
            SeverityRuleOverride(rule_id="R.ALCOA.ACC.001", blocking=True),
            SeverityRuleOverride(rule_id="R.GMP.SIGN.001", blocking=False),
        ),
    )
    assert cfg.is_blocking(rule_id="R.ALCOA.ACC.001", severity="minor")
    assert not cfg.is_blocking(rule_id="R.GMP.SIGN.001", severity="critical")
    assert cfg.is_blocking(rule_id="other", severity="critical")
    assert not cfg.is_blocking(rule_id="other", severity="minor")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "sev.yaml"
    bad.write_text("blocking_severities: not-a-list\n")
    with pytest.raises(ReportingConfigError):
        load_severity_gating(bad)
