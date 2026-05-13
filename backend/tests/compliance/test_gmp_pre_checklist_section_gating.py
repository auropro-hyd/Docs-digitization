"""Smoke test the GMP-PRE5/PRE6 section-type gating.

PR #47 retuned GMP rules 5 (pre-start checklist) and 6 (cross-
contamination checklist) to gate by ``applicable_section_types``
instead of keywords. The new gate is strict: GMP-PRE5 requires
``section_type=pre_production_checklist``; GMP-PRE6 requires
``section_type=cross_contamination_checklist`` AND
``document_type=operation_checklist``. If the segmenter labels
these sections anything else — say ``pre_production`` or
``checklist_before_starting_batch`` — both rules silently drop
out via ``_should_skip`` with no aggregate signal.

This module is a small, deterministic smoke test that doesn't need
a real BPCR doc. Three contracts protected:

  1. ``document_profiles.yaml`` declares the canonical section_types
     GMP-PRE5/PRE6 are scoped to — and includes aliases for the
     real-doc heading variants the segmenter is likely to encounter.

  2. Under the canonical section_type + doc_type combo the
     ``ApplicabilityGate`` does NOT skip the rule.

  3. Under a near-miss section_type (operator-typed variant the
     segmenter might emit without normalization), the gate skips
     the rule WITH a discoverable reason — so an operator looking
     at the rule's ``applicability_trace`` sees the mismatch.
"""

from __future__ import annotations

import pytest


def test_profile_yaml_has_canonical_pre_production_and_cross_contam_sections() -> None:
    """Without these section_types declared in the active profile,
    the segmenter never produces them, the rules never fire, and
    no telemetry surfaces the gap. Pin the declaration."""

    from app.compliance.rules.profiles import load_profiles

    profiles = load_profiles()
    section_types = profiles.known_section_types()
    assert "pre_production_checklist" in section_types, (
        "GMP-PRE5 is scoped to section_type=pre_production_checklist "
        "but that value isn't declared in document_profiles.yaml — "
        "the rule is unreachable"
    )
    assert "cross_contamination_checklist" in section_types, (
        "GMP-PRE6 is scoped to section_type=cross_contamination_checklist "
        "but that value isn't declared in document_profiles.yaml — "
        "the rule is unreachable"
    )


@pytest.mark.parametrize("alias,expected_canonical", [
    ("checklist_before_starting_batch", "pre_production_checklist"),
    ("pre_batch_checklist", "pre_production_checklist"),
    ("line_clearance_before_batch", "pre_production_checklist"),
    ("cross_contamination_check", "cross_contamination_checklist"),
])
def test_profile_aliases_normalise_real_doc_headers(
    alias: str, expected_canonical: str,
) -> None:
    """The segmenter labels sections by their heading text. Without
    aliases, every real-doc variant becomes its own opaque
    section_type that GMP-PRE5/PRE6 will skip. Pin the alias
    coverage so the rules survive normal segmenter naming
    variation."""

    from app.compliance.rules.profiles import normalize_section_type

    assert normalize_section_type(alias) == expected_canonical, (
        f"alias {alias!r} doesn't normalize to {expected_canonical!r} — "
        f"if the segmenter emits this raw value, the GMP-PRE rule's "
        f"applicable_section_types gate will silently drop the rule"
    )


def test_gmp_pre5_fires_on_pre_production_checklist_section() -> None:
    """Happy path: when a page lives in a section the segmenter
    labelled ``pre_production_checklist``, GMP-PRE5's applicability
    gate must NOT skip — the rule reaches the evaluator."""

    from app.compliance.applicability import ApplicabilityGate
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    pre5 = next(
        (rule for rule in r.get_rules("gmp") if rule.id == "GMP-PRE5"),
        None,
    )
    assert pre5 is not None, "GMP-PRE5 must exist in gmp_rules.yaml"

    gate = ApplicabilityGate()
    skip_reason, trace = gate._should_skip(
        pre5,
        document_type="operation_checklist",
        page_type="form",
        section_type="pre_production_checklist",
        extraction={"page_num": 5, "markdown": "Check List Before Starting"},
        include_keyword_gate=False,
    )

    assert skip_reason is None, (
        f"GMP-PRE5 must not skip on its declared section_type "
        f"(got skip reason: {skip_reason!r})"
    )


def test_gmp_pre5_skips_near_miss_section_type_with_traceable_reason() -> None:
    """Control case: the segmenter typed the section ``pre_production``
    (no ``_checklist`` suffix — common operator slip). GMP-PRE5
    must skip BUT the trace must name the section_type mismatch so
    the operator can see "the rule wanted X, segment had Y" without
    spelunking the YAML."""

    from app.compliance.applicability import ApplicabilityGate
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    pre5 = next(rule for rule in r.get_rules("gmp") if rule.id == "GMP-PRE5")

    gate = ApplicabilityGate()
    skip_reason, trace = gate._should_skip(
        pre5,
        document_type="operation_checklist",
        page_type="form",
        section_type="pre_production",  # near-miss — missing _checklist suffix
        extraction={"page_num": 5, "markdown": "x"},
        include_keyword_gate=False,
    )

    assert skip_reason is not None, "near-miss section_type must skip"
    # The trace must name pre_production_checklist so the operator
    # sees the expected vs actual mismatch without reading code.
    trace_text = " ".join(trace).lower()
    assert "pre_production_checklist" in trace_text, (
        f"applicability_trace must name the expected section_type "
        f"so the operator can diagnose without reading YAML; got: {trace}"
    )


def test_gmp_pre6_fires_on_cross_contamination_section() -> None:
    """Same shape for GMP-PRE6 — its declared section_type
    must satisfy the applicability gate."""

    from app.compliance.applicability import ApplicabilityGate
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    pre6 = next(
        (rule for rule in r.get_rules("gmp") if rule.id == "GMP-PRE6"),
        None,
    )
    assert pre6 is not None, "GMP-PRE6 must exist in gmp_rules.yaml"

    gate = ApplicabilityGate()
    skip_reason, _trace = gate._should_skip(
        pre6,
        document_type="operation_checklist",
        page_type="form",
        section_type="cross_contamination_checklist",
        extraction={"page_num": 9, "markdown": "Check List for Cross Contamination"},
        include_keyword_gate=False,
    )

    assert skip_reason is None, (
        f"GMP-PRE6 must not skip on its declared section_type "
        f"(got skip reason: {skip_reason!r})"
    )


def test_gmp_pre6_skips_on_wrong_document_type() -> None:
    """GMP-PRE6 has BOTH applicable_document_types AND
    applicable_section_types. A page with the right section_type
    but a non-operation_checklist doc_type must still skip — verify
    the doc_type gate fires independently."""

    from app.compliance.applicability import ApplicabilityGate
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    pre6 = next(rule for rule in r.get_rules("gmp") if rule.id == "GMP-PRE6")

    gate = ApplicabilityGate()
    skip_reason, _trace = gate._should_skip(
        pre6,
        document_type="batch_record",  # wrong doc_type
        page_type="form",
        section_type="cross_contamination_checklist",
        extraction={"page_num": 9, "markdown": "x"},
        include_keyword_gate=False,
    )

    assert skip_reason is not None
    assert "operation_checklist" in skip_reason or "batch_record" in skip_reason
