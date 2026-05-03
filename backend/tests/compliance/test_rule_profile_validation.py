"""Pin the startup validator's coverage of cross_section_requirements.

The validator runs at FastAPI startup (``main.py:43``) and surfaces
any rule whose applicability metadata references an unknown
section_type, document_type, or — newly — an unknown
cross_section_requirement (string-id) or malformed inline dict
shape.

Without these checks Akhilesh's "I'll add 12 more rules" workflow
silently rots into rules that never fire. The validator turns the
rot into a single warning line operators see at boot.
"""

from __future__ import annotations

import pytest

from app.compliance.rules.profiles import (
    load_profiles,
    validate_compliance_configs,
)
from app.compliance.rules.registry import AuditRule, RuleRegistry


class _FakeRegistry:
    """Duck-typed registry holding a single test rule.

    The validator reads ``.agents`` and ``.get_rules(agent)``;
    mocking the protocol here keeps the test independent of the
    real registry's filesystem-backed construction.
    """

    def __init__(self, rule: AuditRule) -> None:
        self._rule = rule

    @property
    def agents(self) -> list[str]:
        return ["test_agent"]

    def get_rules(self, _agent: str) -> list[AuditRule]:
        return [self._rule]


def _registry_with(rule: AuditRule) -> RuleRegistry:
    return _FakeRegistry(rule)  # type: ignore[return-value]


def _make_rule(**csr_kwargs) -> AuditRule:
    return AuditRule(
        id="TEST-1",
        number=1,
        category="test",
        category_display="Test",
        agent="test_agent",
        text="test rule",
        **csr_kwargs,
    )


@pytest.fixture(autouse=True)
def _reset_profiles_cache():
    load_profiles.cache_clear()
    yield
    load_profiles.cache_clear()


# ── Production rules pass cleanly ────────────────────────────


def test_production_registry_passes_validation_strict() -> None:
    """The current production rule set must validate cleanly under
    strict mode — any drift between rule files and document_profiles
    should fail the boot before it reaches a customer.
    """
    from app.compliance.rules.registry import get_registry

    # Should not raise.
    validate_compliance_configs(get_registry(), strict=True)


# ── String-shape CSR validation ──────────────────────────────


def test_string_csr_unknown_id_is_flagged_in_strict_mode() -> None:
    rule = _make_rule(cross_section_requirements=["nonexistent_requirement_id"])
    with pytest.raises(ValueError, match="unknown requirement_id"):
        validate_compliance_configs(_registry_with(rule), strict=True)


def test_string_csr_known_id_passes() -> None:
    rule = _make_rule(cross_section_requirements=["material_usage_vs_dispensing"])
    validate_compliance_configs(_registry_with(rule), strict=True)


# ── Dict-shape CSR validation ────────────────────────────────


def test_dict_csr_with_known_section_and_doc_passes() -> None:
    rule = _make_rule(cross_section_requirements=[
        {"section_type": "material_request", "in_document_type": "raw_material_request"},
    ])
    validate_compliance_configs(_registry_with(rule), strict=True)


def test_dict_csr_with_unknown_section_is_flagged() -> None:
    rule = _make_rule(cross_section_requirements=[
        {"section_type": "totally_made_up_section", "in_document_type": "batch_record"},
    ])
    with pytest.raises(ValueError, match="section_type 'totally_made_up_section' is unknown"):
        validate_compliance_configs(_registry_with(rule), strict=True)


def test_dict_csr_with_unknown_doc_is_flagged() -> None:
    rule = _make_rule(cross_section_requirements=[
        {"section_type": "material_request", "in_document_type": "made_up_doc"},
    ])
    with pytest.raises(ValueError, match="in_document_type 'made_up_doc' is unknown"):
        validate_compliance_configs(_registry_with(rule), strict=True)


def test_dict_csr_with_only_doc_type_passes() -> None:
    """Whole-document mode (``section_type=""``) is valid for
    documents like ipc_report whose profile carries no
    expected_sections."""
    rule = _make_rule(cross_section_requirements=[
        {"section_type": "", "in_document_type": "ipc_report"},
    ])
    validate_compliance_configs(_registry_with(rule), strict=True)


def test_dict_csr_with_neither_field_is_flagged() -> None:
    rule = _make_rule(cross_section_requirements=[
        {"section_type": "", "in_document_type": ""},
    ])
    with pytest.raises(ValueError, match="empty"):
        validate_compliance_configs(_registry_with(rule), strict=True)


# ── Non-strict mode just logs ────────────────────────────────


def test_non_strict_mode_logs_warning_does_not_raise(caplog) -> None:
    """In dev iteration the validator must not block boot — drift
    surfaces as a single WARNING and the app continues. The runtime
    applicability gate filters mismatched rules anyway."""
    rule = _make_rule(cross_section_requirements=["nonexistent_requirement"])

    import logging
    caplog.set_level(logging.WARNING, logger="app.compliance.rules.profiles")

    # Should NOT raise.
    validate_compliance_configs(_registry_with(rule), strict=False)

    assert any(
        "validation_drift" in record.message
        for record in caplog.records
    ), "non-strict mode must emit a single WARNING summarizing the drift"
