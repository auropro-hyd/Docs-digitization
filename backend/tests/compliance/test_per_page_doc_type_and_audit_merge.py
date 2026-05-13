"""Pin Akhilesh's a2f690e fixes + the silent-skip telemetry layered on top.

Two load-bearing behaviours from 010's second commit:

1. **Per-page doc_type derivation** — previously every agent passed
   a static ``document_type="batch_record"`` into
   ``run_agent_evaluation``, so rules scoped to ``operation_checklist``
   sections silently skipped on multi-section PDFs (a batch_record
   followed by checklists in the same packet). The fix derives
   ``effective_doc_type`` per page from ``section_map``. This means an
   operation_checklist-scoped rule must FIRE on a checklist page and
   SKIP on a batch_record page in the same document.

2. **Audit-trail merge on severity upgrade** — when two evaluations
   for the same rule_id resolve to different statuses, the merged
   ``RuleResult`` must adopt the higher-severity status's reasoning
   and evidence. Pre-fix, the new reasoning was dropped because the
   merge only filled empty fields. A non_compliant verdict surfaced
   with a "looks fine" justification from the earlier compliant pass.

3. **Silent-skip telemetry** — the per-page doc_type fix introduces a
   new failure mode: a page with no ``document_type`` (segmentation
   missed it or the section is ``unknown``) silently skips every
   rule with ``applicable_document_types``. The new
   ``compliance.rule_skipped_missing_doc_type`` event records the
   affected page and rule_ids so the gap is queryable.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.compliance.evaluator import assemble_agent_report
from app.compliance.models import (
    ComplianceFinding,
    RuleBatchResult,
    RuleEvaluation,
)
from app.compliance.rules.registry import AuditRule


def _rule(
    rule_id: str,
    applicable_section_types: list[str] | None = None,
    applicable_document_types: list[str] | None = None,
) -> AuditRule:
    return AuditRule(
        id=rule_id,
        number=1,
        category="checklist",
        category_display="Checklist",
        agent="checklist",
        text="dummy",
        applicable_section_types=applicable_section_types or [],
        applicable_document_types=applicable_document_types or [],
    )


# ── Per-page doc_type derivation ────────────────────────────────


def test_section_map_carries_document_type_for_per_page_gating() -> None:
    """``build_page_to_section`` must include ``document_type`` in
    every page entry so the evaluator's per-page derivation has
    something to read. Akhilesh's commit added the field; this test
    pins it so a future refactor doesn't silently drop it."""

    from app.compliance.models import DocumentSection, DocumentSegmentation
    from app.compliance.segmentation import build_page_to_section

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="bpcr_part",
                name="BPCR",
                section_type="manufacturing_operations",
                document_type="batch_record",
                start_page=1, end_page=3,
            ),
            DocumentSection(
                section_id="reactor_chklst",
                name="Reactor Checklist",
                section_type="reactor_checklist",
                document_type="operation_checklist",
                start_page=4, end_page=6,
            ),
        ],
    )
    page_map = build_page_to_section(seg)

    assert page_map[1]["document_type"] == "batch_record"
    assert page_map[5]["document_type"] == "operation_checklist"


def test_applicability_filter_uses_per_page_effective_doc_type() -> None:
    """The applicability gate must accept an empty-string doc_type
    and skip rules with ``applicable_document_types`` — the
    fallback path when a page isn't in section_map. Pre-a2f690e
    this masked itself behind the static ``"batch_record"`` default;
    post-fix it surfaces as not_applicable."""

    from app.compliance.applicability import ApplicabilityGate

    gate = ApplicabilityGate()
    rule = _rule("CHK-1", applicable_document_types=["operation_checklist"])

    # On a checklist page: doc_type matches → don't skip.
    skip_reason, _ = gate._should_skip(
        rule,
        document_type="operation_checklist",
        page_type="form",
        section_type="reactor_checklist",
        extraction={"page_num": 5, "markdown": "x"},
        include_keyword_gate=False,
    )
    assert skip_reason is None, (
        f"rule must fire on its own doc_type (reason={skip_reason})"
    )

    # On a batch_record page: doc_type mismatches → skip.
    skip_reason, _ = gate._should_skip(
        rule,
        document_type="batch_record",
        page_type="form",
        section_type="manufacturing_operations",
        extraction={"page_num": 1, "markdown": "x"},
        include_keyword_gate=False,
    )
    assert skip_reason is not None
    assert "operation_checklist" in skip_reason or "batch_record" in skip_reason

    # On a page with no doc_type assignment: still skip a
    # doc_type-scoped rule. This is the new silent-skip class the
    # telemetry below makes visible.
    skip_reason, _ = gate._should_skip(
        rule,
        document_type="",
        page_type="form",
        section_type="",
        extraction={"page_num": 24, "markdown": "x"},
        include_keyword_gate=False,
    )
    assert skip_reason is not None


# ── Audit-trail merge order ────────────────────────────────────


def test_assemble_report_overwrites_compliant_reasoning_when_upgrading_to_non_compliant() -> None:
    """The fix from a2f690e: when severity upgrades (compliant →
    non_compliant), the new reasoning + evidence replace the
    earlier ones rather than being dropped because "the field
    isn't empty". Pre-fix this surfaced non_compliant findings with
    misleading "looks fine" reasoning from a prior compliant pass."""

    rule = _rule("CHK-1")
    # Page 1: compliant verdict with optimistic reasoning.
    page1 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.9,
        reasoning="No issues observed on this page.",
        evidence="All fields populated.",
    )])
    # Page 2: non_compliant verdict with the actual finding.
    page2 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="non_compliant", confidence=0.85,
        reasoning="Operator signature missing on row 4.",
        evidence="Row 4: 'Done by' cell is blank.",
    )])

    report = assemble_agent_report(
        agent="checklist",
        all_rules=[rule],
        batch_results=[
            ("batch-a", 1, page1),
            ("batch-b", 2, page2),
        ],
        pages_reviewed=[1, 2],
    )

    by_id = {ev.rule_id: ev for ev in report.all_evaluations}
    assert by_id["CHK-1"].status == "non_compliant"
    # The non_compliant reasoning must surface — not the optimistic
    # compliant one from page 1.
    assert "signature missing" in by_id["CHK-1"].reasoning, (
        f"audit-trail merge regressed: reasoning didn't upgrade. "
        f"Got: {by_id['CHK-1'].reasoning!r}"
    )
    assert "Row 4" in by_id["CHK-1"].evidence


def test_assemble_report_audit_merge_independent_of_input_order() -> None:
    """The merge must converge regardless of which page lands in
    the batch_results list first. Page-order-dependent verdicts
    would be a flaky test in any HITL workflow."""

    rule = _rule("CHK-1")
    high_sev = RuleEvaluation(
        rule_id="CHK-1", status="non_compliant", confidence=0.85,
        reasoning="Real finding.", evidence="Row 4 blank.",
    )
    low_sev = RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.9,
        reasoning="Looks fine.", evidence="All populated.",
    )

    for order_label, (first, second) in [
        ("high→low", (high_sev, low_sev)),
        ("low→high", (low_sev, high_sev)),
    ]:
        report = assemble_agent_report(
            agent="checklist",
            all_rules=[rule],
            batch_results=[
                ("a", 1, RuleBatchResult(evaluations=[first])),
                ("b", 2, RuleBatchResult(evaluations=[second])),
            ],
            pages_reviewed=[1, 2],
        )
        ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
        assert ev.status == "non_compliant", (
            f"order={order_label}: expected non_compliant, got {ev.status}"
        )
        assert "Real finding" in ev.reasoning, (
            f"order={order_label}: non_compliant reasoning was dropped. "
            f"Got: {ev.reasoning!r}"
        )


# ── Silent-skip telemetry ──────────────────────────────────────


@pytest.mark.asyncio
async def test_rule_skipped_missing_doc_type_event_fires_when_page_unassigned(
    tmp_path,
) -> None:
    """When a page has no document_type in ``section_map`` AND the
    batch contains rules with ``applicable_document_types``, the
    evaluator must fire ``compliance.rule_skipped_missing_doc_type``
    so the gap is queryable in ``telemetry.json``. Without this the
    per-page doc_type fix introduced a new silent-skip class —
    rules dropping out without a single aggregate signal."""

    from app.compliance.evaluator import run_agent_evaluation
    from app.compliance.rules.registry import RuleBatch
    from app.observability.run_telemetry import telemetry_run
    from unittest.mock import AsyncMock
    import json

    rule = _rule(
        "CHK-1",
        applicable_document_types=["operation_checklist"],
    )
    batch = RuleBatch(
        batch_id="b1",
        category="checklist",
        agent="checklist",
        rules=[rule],
    )
    # Page 1 IS in section_map; page 2 is NOT — the telemetry should
    # fire for page 2 only.
    section_map = {
        1: {"document_type": "operation_checklist", "section_type": "reactor_checklist"},
    }
    extractions = [
        {"page_num": 1, "markdown": "checklist page"},
        {"page_num": 2, "markdown": "orphaned page"},
    ]
    fake_llm = AsyncMock()

    doc_id = "test-doc-orphan-page"
    doc_dir = tmp_path / doc_id

    with telemetry_run(doc_id, doc_dir, name="test"):
        await run_agent_evaluation(
            agent="checklist",
            batches=[batch],
            extractions=extractions,
            llm=fake_llm,
            max_concurrent=2,
            section_map=section_map,
        )

    telemetry_path = doc_dir / "telemetry-test.json"
    assert telemetry_path.exists(), "telemetry sink didn't persist"
    data = json.loads(telemetry_path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    skip_events = [
        e for e in events
        if e.get("event") == "compliance.rule_skipped_missing_doc_type"
    ]
    assert skip_events, (
        "evaluator did not emit a "
        "compliance.rule_skipped_missing_doc_type event for page 2"
    )
    # Per-event payload lives under ``fields`` in the sink format.
    pages_flagged = {e.get("fields", {}).get("page_num") for e in skip_events}
    assert 2 in pages_flagged
    assert 1 not in pages_flagged, (
        "event must not fire for pages that DO have a document_type"
    )
    # The CHK-1 rule_id must appear in the skipped list so the
    # operator can trace which rules degraded.
    rule_ids: list[str] = []
    for e in skip_events:
        rule_ids.extend(e.get("fields", {}).get("skipped_rule_ids", []))
    assert "CHK-1" in rule_ids


def test_assemble_report_keeps_existing_reasoning_when_new_evaluation_empty() -> None:
    """The ``or`` short-circuit guards against the new evaluation
    overwriting real content with an empty string. If the higher-
    severity verdict arrives with empty reasoning, the existing
    detail must survive."""

    rule = _rule("CHK-1")
    page1 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.9,
        reasoning="Detail from page 1.", evidence="Evidence A.",
    )])
    # Page 2: upgrade but with empty reasoning (e.g. a structured-
    # output failure mode).
    page2 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="non_compliant", confidence=0.5,
        reasoning="", evidence="",
    )])

    report = assemble_agent_report(
        agent="checklist",
        all_rules=[rule],
        batch_results=[("a", 1, page1), ("b", 2, page2)],
        pages_reviewed=[1, 2],
    )
    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "non_compliant"
    # Don't lose the existing detail just because the upgrade had none.
    assert ev.reasoning == "Detail from page 1."
    assert ev.evidence == "Evidence A."
